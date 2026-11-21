"""Tests for M3: coordinator, new-mail events, sensors, push state, action counter, diagnostics and repairs."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed

from custom_components.gmx_mail.const import (
    CONF_ALLOWLIST,
    CONF_EVENT_INCLUDE_HEADERS,
    CONF_IDLE_ENABLED,
    CONF_IMAP_PORT,
    CONF_IMAP_SECURITY,
    CONF_SEND_MODE,
    DOMAIN,
    EVENT_NEW_MESSAGE,
    ISSUE_FOLDER_MAPPING_AMBIGUOUS,
    ISSUE_SERVER_RULES_UNAVAILABLE,
    ISSUE_UIDVALIDITY_CHANGED,
    MAX_NEW_MESSAGE_EVENTS,
)
from custom_components.gmx_mail.diagnostics import async_get_config_entry_diagnostics

from .fakes import EMAIL, PASSWORD, FakeIdleServer, FakeImapServer, plain_message


async def settle(hass: HomeAssistant) -> None:
    """Let background tasks (the IDLE watcher is one; async_block_till_done does not wait for it) run."""
    for _ in range(10):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up an entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)


def state_of(hass: HomeAssistant, entry: MockConfigEntry, key: str, platform: str = "sensor") -> Any:
    """Return the state of an entity by its unique id."""
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id is not None, key
    return hass.states.get(entity_id)


async def refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run a coordinator update now."""
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


@pytest.fixture
def mailbox(imap_server: FakeImapServer) -> FakeImapServer:
    """Inbox with three messages (one read) and a spam folder with two."""
    inbox = imap_server.mailbox("INBOX", uidvalidity=777)
    inbox.messages[1] = plain_message(flags=(b"\\Seen",))
    inbox.messages[2] = plain_message()
    inbox.messages[3] = plain_message()
    junk = imap_server.mailbox("Spamverdacht")
    junk.messages[1] = plain_message()
    junk.messages[2] = plain_message()
    return imap_server


async def test_sensors_and_connection(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Counters come from STATUS; the connection sensor shows the push state."""
    await setup_entry(hass, config_entry)
    assert state_of(hass, config_entry, "unseen_inbox").state == "2"
    assert state_of(hass, config_entry, "junk_messages").state == "2"
    assert state_of(hass, config_entry, "last_sync").state != STATE_UNAVAILABLE
    assert state_of(hass, config_entry, "actions_today").state == "0"
    connection = state_of(hass, config_entry, "connection", "binary_sensor")
    assert connection.state == STATE_ON
    assert connection.attributes["push"] == "connected"
    assert config_entry.runtime_data.limiter.in_use == 2  # work connection + IDLE


async def test_no_spam_folder_means_unavailable(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """If the spam folder cannot be read, its sensor is unavailable (not 0)."""
    await setup_entry(hass, config_entry)
    assert state_of(hass, config_entry, "junk_messages").state == STATE_UNAVAILABLE
    assert state_of(hass, config_entry, "unseen_inbox").state == "0"


async def test_new_message_events(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Existing mail is the baseline; only later mail is announced, with a reference and without headers."""
    events = async_capture_events(hass, EVENT_NEW_MESSAGE)
    await setup_entry(hass, config_entry)
    assert events == []
    mailbox.mailbox("INBOX").messages[4] = plain_message(subject="Neu")
    await refresh(hass, config_entry)
    assert [e.data for e in events] == [
        {"entry_id": config_entry.entry_id, "folder": "INBOX", "ref": {"folder": "INBOX", "uid": 4, "uidvalidity": 777}}
    ]
    await refresh(hass, config_entry)
    assert len(events) == 1  # not announced twice
    assert state_of(hass, config_entry, "unseen_inbox").state == "3"


async def test_event_headers_only_with_option(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Sender and subject are added only if event_include_headers is on."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={**config_entry.options, CONF_EVENT_INCLUDE_HEADERS: True})
    events = async_capture_events(hass, EVENT_NEW_MESSAGE)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mailbox.mailbox("INBOX").messages[4] = plain_message(subject="Rechnung März", sender="Shop <shop@example.com>")
    await refresh(hass, config_entry)
    assert events[0].data["subject"] == "Rechnung März"
    assert events[0].data["from"] == "Shop <shop@example.com>"


async def test_flood_protection(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """At most 20 events per update (the newest); the rest is only counted."""
    events = async_capture_events(hass, EVENT_NEW_MESSAGE)
    await setup_entry(hass, config_entry)
    inbox = mailbox.mailbox("INBOX")
    for uid in range(10, 40):
        inbox.messages[uid] = plain_message()
    await refresh(hass, config_entry)
    assert len(events) == MAX_NEW_MESSAGE_EVENTS
    assert [e.data["ref"]["uid"] for e in events] == list(range(20, 40))
    assert config_entry.runtime_data.coordinator.skipped_total == 10


async def test_uidvalidity_change(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """A rebuilt inbox creates a repair issue and no flood of events."""
    events = async_capture_events(hass, EVENT_NEW_MESSAGE)
    await setup_entry(hass, config_entry)
    inbox = mailbox.mailbox("INBOX", uidvalidity=999)
    inbox.messages[50] = plain_message()
    await refresh(hass, config_entry)
    assert events == []
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_UIDVALIDITY_CHANGED}_{config_entry.entry_id}")
    assert issue is not None and issue.translation_placeholders == {"account": EMAIL, "folder": "INBOX"}
    inbox.messages[51] = plain_message()
    await refresh(hass, config_entry)
    assert [e.data["ref"] for e in events] == [{"folder": "INBOX", "uid": 51, "uidvalidity": 999}]


async def test_mail_received_while_unloaded_is_announced(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, freezer: FrozenDateTimeFactory
) -> None:
    """The last seen UID is stored, so mail that arrived during a restart is announced afterwards."""
    events = async_capture_events(hass, EVENT_NEW_MESSAGE)
    await setup_entry(hass, config_entry)
    freezer.tick(timedelta(seconds=10))  # let the delayed store write happen
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    mailbox.mailbox("INBOX").messages[7] = plain_message()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert [e.data["ref"]["uid"] for e in events] == [7]


async def test_idle_push_triggers_refresh(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, idle_server: FakeIdleServer, freezer: FrozenDateTimeFactory
) -> None:
    """EXISTS during IDLE makes the coordinator fetch within the 5 s debounce (no 5-minute wait)."""
    events = async_capture_events(hass, EVENT_NEW_MESSAGE)
    await setup_entry(hass, config_entry)
    await idle_server.started.wait()
    client = idle_server.clients[-1]
    assert client.selected == ["INBOX"]
    mailbox.mailbox("INBOX").messages[8] = plain_message()
    client.push(b"4 EXISTS")
    await settle(hass)
    freezer.tick(timedelta(seconds=6))  # the catch-up refresh after connecting started the debounce cooldown
    async_fire_time_changed(hass)
    await settle(hass)
    assert [e.data["ref"]["uid"] for e in events] == [8]


async def test_poll_failure_and_recovery(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Unreachable server: counters unavailable, connection off; back to normal afterwards."""
    await setup_entry(hass, config_entry)
    mailbox.status_error = TimeoutError()
    await refresh(hass, config_entry)
    assert state_of(hass, config_entry, "unseen_inbox").state == STATE_UNAVAILABLE
    assert state_of(hass, config_entry, "connection", "binary_sensor").state == STATE_OFF
    assert config_entry.runtime_data.coordinator.last_error is not None
    mailbox.status_error = None
    config_entry.runtime_data.worker._backoff.reset()
    await refresh(hass, config_entry)
    assert state_of(hass, config_entry, "unseen_inbox").state == "2"
    assert state_of(hass, config_entry, "connection", "binary_sensor").state == STATE_ON


async def test_push_disabled_or_unsupported(hass: HomeAssistant, imap_server: FakeImapServer, idle_server: FakeIdleServer) -> None:
    """IDLE off in the options -> 'disabled'; STARTTLS (no aioimaplib support) -> 'unsupported'; polling works."""
    from custom_components.gmx_mail.const import DEFAULT_OPTIONS  # noqa: PLC0415

    from .conftest import ENTRY_DATA  # noqa: PLC0415

    disabled = MockConfigEntry(
        domain=DOMAIN,
        title="a@gmx.test",
        unique_id="a@gmx.test",
        data=dict(ENTRY_DATA),
        options={**DEFAULT_OPTIONS, CONF_IDLE_ENABLED: False},
    )
    await setup_entry(hass, disabled)
    assert state_of(hass, disabled, "connection", "binary_sensor").attributes["push"] == "disabled"
    starttls_data = {**ENTRY_DATA, CONF_IMAP_PORT: 143, CONF_IMAP_SECURITY: "starttls"}
    starttls = MockConfigEntry(domain=DOMAIN, title="b@gmx.test", unique_id="b@gmx.test", data=starttls_data, options=dict(DEFAULT_OPTIONS))
    await setup_entry(hass, starttls)
    assert state_of(hass, starttls, "connection", "binary_sensor").attributes["push"] == "unsupported"
    assert idle_server.clients == []
    assert state_of(hass, starttls, "unseen_inbox").state == "0"


async def test_idle_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, idle_server: FakeIdleServer
) -> None:
    """If GMX rejects the IDLE login, reauth starts and the repair issue appears."""
    idle_server.password = "revoked"
    await setup_entry(hass, config_entry)
    assert config_entry.runtime_data.push_state.value == "auth_failed"
    assert state_of(hass, config_entry, "connection", "binary_sensor").attributes["push"] == "auth_failed"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_actions_today_counter(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, freezer: FrozenDateTimeFactory
) -> None:
    """Every executed action is counted; the counter resets at local midnight (Home Assistant's time zone)."""
    await hass.config.async_set_time_zone("Europe/Berlin")  # tests default to US/Pacific
    freezer.move_to("2026-11-21 12:00:00+01:00")
    await setup_entry(hass, config_entry)
    for _ in range(3):
        await hass.services.async_call(
            DOMAIN, "get_status", {"config_entry_id": config_entry.entry_id}, blocking=True, return_response=True
        )
    state = state_of(hass, config_entry, "actions_today")
    assert state.state == "3"
    assert state.attributes["by_level"] == {"read": 3}
    status = await hass.services.async_call(
        DOMAIN, "get_status", {"config_entry_id": config_entry.entry_id}, blocking=True, return_response=True
    )
    assert status is not None
    assert status["data"]["push_state"] == "connected"
    assert status["data"]["idle_active"] is True
    assert status["data"]["last_sync"] is not None
    freezer.move_to("2026-11-22 00:00:05+01:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state_of(hass, config_entry, "actions_today").state == "0"


async def test_diagnostics_are_redacted(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """No password, address or allowlist entry in the diagnostics."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_SEND_MODE: "allowlist", CONF_ALLOWLIST: ["friend@example.org"]}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    text = str(diagnostics)
    assert PASSWORD not in text and EMAIL not in text and "friend@example.org" not in text
    assert diagnostics["options"]["allowlist_entries"] == 1
    assert diagnostics["entry"]["data"]["imap_host"] == "imap.gmx.net"
    assert diagnostics["server"]["append_limit"] == 29360128
    assert diagnostics["coordinator"]["unseen_inbox"] == 2
    assert diagnostics["push"]["state"] == "connected"
    assert diagnostics["work_connection"]["connections_in_use"] == 2


async def test_repairs_lifecycle(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Ambiguous folders and the filter-rules notice are created; removing the entry cleans up."""
    mailbox.folders.append(((b"\\HasNoChildren",), b"/", "Sent"))
    await setup_entry(hass, config_entry)
    registry = ir.async_get(hass)
    ambiguous = registry.async_get_issue(DOMAIN, f"{ISSUE_FOLDER_MAPPING_AMBIGUOUS}_{config_entry.entry_id}")
    assert ambiguous is not None
    assert ambiguous.translation_placeholders == {"account": EMAIL, "roles": "sent → Gesendet"}
    assert registry.async_get_issue(DOMAIN, ISSUE_SERVER_RULES_UNAVAILABLE) is not None
    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_FOLDER_MAPPING_AMBIGUOUS}_{config_entry.entry_id}") is None
    assert registry.async_get_issue(DOMAIN, ISSUE_SERVER_RULES_UNAVAILABLE) is None
