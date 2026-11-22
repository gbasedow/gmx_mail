"""Tests for the deleting actions (M5) as Home Assistant services: permissions, confirmation codes, limits, audit."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from imapclient.exceptions import IMAPClientAbortError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.gmx_mail.const import (
    CONF_ALLOW_PERMANENT_DELETE,
    CONF_MAX_DELETE_PER_DAY,
    CONF_PERMISSIONS,
    DEFAULT_OPTIONS,
    DOMAIN,
    EVENT_ACTION_EXECUTED,
)

from .fakes import FakeImapServer, plain_message


@pytest.fixture
def mailbox(imap_server: FakeImapServer) -> FakeImapServer:
    """INBOX with three messages, the trash with two; all GMX folders exist."""
    inbox = imap_server.mailbox("INBOX", uidvalidity=777)
    for uid in (1, 2, 3):
        inbox.messages[uid] = plain_message(subject=f"m{uid}", message_id=f"<m{uid}@example.org>")
    trash_box = imap_server.mailbox("Gelöscht", uidvalidity=900)
    for uid in (1, 2):
        trash_box.messages[uid] = plain_message(subject=f"t{uid}", message_id=f"<t{uid}@example.org>")
    for name in ("Archiv", "Spamverdacht", "Entwürfe", "Gesendet"):
        imap_server.mailbox(name, uidvalidity=901)
    return imap_server


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, **options: Any) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**DEFAULT_OPTIONS, CONF_PERMISSIONS: ["read", "delete"], CONF_ALLOW_PERMANENT_DELETE: True, **options}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def ref(uid: int, folder: str = "INBOX", validity: int = 777) -> dict[str, Any]:
    """Build a message reference."""
    return {"folder": folder, "uid": uid, "uidvalidity": validity}


def tref(uid: int) -> dict[str, Any]:
    """Reference of a message in the trash."""
    return ref(uid, "Gelöscht", 900)


async def call(hass: HomeAssistant, entry: MockConfigEntry, service: str, **data: Any) -> dict[str, Any]:
    """Call an action with return_response (as ha-mcp does)."""
    response = await hass.services.async_call(
        DOMAIN, service, {"config_entry_id": entry.entry_id, **data}, blocking=True, return_response=True
    )
    assert isinstance(response, dict)
    return response


async def refused(hass: HomeAssistant, entry: MockConfigEntry, service: str, **data: Any) -> str:
    """Call an action that must be refused and return the translation key."""
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, entry, service, **data)
    return str(err.value.translation_key)


def _cmds(server: FakeImapServer, *names: str) -> list[tuple[Any, ...]]:
    return [c for c in server.commands if c[0] in names]


async def test_delete_actions_need_the_delete_level(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Read and organise do not include deleting; nothing reaches the server."""
    await _setup(hass, config_entry, **{CONF_PERMISSIONS: ["read", "organize"]})
    for service, data in (
        ("trash", {"refs": [ref(1)]}),
        ("delete_permanently", {"refs": [tref(1)]}),
        ("delete_folder", {"path": "Archiv"}),
    ):
        assert await refused(hass, config_entry, service, **data) == "permission_disabled"
    assert not _cmds(mailbox, "move", "add_flags", "uid_expunge", "delete_folder")


async def test_trash_counts_and_audits(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Trash returns new references, counts towards the daily limit and is audited without content."""
    await _setup(hass, config_entry)
    events = async_capture_events(hass, EVENT_ACTION_EXECUTED)
    result = await call(hass, config_entry, "trash", refs=[ref(1), ref(2), ref(9)])
    data = result["data"]
    assert data["trashed"] == 2 and data["trash_folder"] == "Gelöscht"
    assert data["new_refs"] == [tref(3), tref(4)] and data["missing"] == [ref(9)]
    status = await call(hass, config_entry, "get_status")
    assert status["data"]["limits_used"]["deleted_today"] == 2
    assert events[-1].data["action"] == "trash" and events[-1].data["count"] == 2
    assert "m1" not in str(events[-1].data)  # no subjects
    assert await refused(hass, config_entry, "trash", refs=[tref(1)]) == "already_in_trash"


async def test_permanent_delete_two_steps(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Preview + code, then deletion of exactly these messages; the code works once and only for them."""
    await _setup(hass, config_entry)
    first = await call(hass, config_entry, "delete_permanently", refs=[tref(2), tref(1)])
    preview = first["data"]["preview"]
    assert preview["count"] == 2 and [m["subject"] for m in preview["messages"]] == ["t1", "t2"]
    assert first["data"]["expires_in"] == 300 and first["content_trust"] == "untrusted_external_email"
    code = first["data"]["confirm_token"]
    assert not _cmds(mailbox, "add_flags", "uid_expunge")  # the preview deletes nothing

    assert await refused(hass, config_entry, "delete_permanently", refs=[tref(1)], confirm_token=code) == "confirm_token_invalid"
    done = await call(hass, config_entry, "delete_permanently", refs=[tref(1), tref(2)], confirm_token=code)  # order does not matter
    assert done["data"] == {"deleted": 2, "missing": []}
    assert not mailbox.mailboxes["Gelöscht"].messages
    assert await refused(hass, config_entry, "delete_permanently", refs=[tref(1), tref(2)], confirm_token=code) == "confirm_token_invalid"

    log = (await call(hass, config_entry, "get_audit_log"))["data"]["entries"]
    assert [(e["action"], e["outcome"]) for e in log[:4]] == [
        ("delete_permanently", "rejected"),
        ("delete_permanently", "ok"),
        ("delete_permanently", "rejected"),
        ("delete_permanently", "preview"),
    ]
    assert log[0]["error"] == "confirm_token_invalid"


async def test_permanent_delete_rules(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Option off: refused; outside trash/spam: refused; nothing left to delete: no code."""
    await _setup(hass, config_entry, **{CONF_ALLOW_PERMANENT_DELETE: False})
    assert await refused(hass, config_entry, "delete_permanently", refs=[tref(1)]) == "permanent_delete_disabled"
    await hass.config_entries.async_unload(config_entry.entry_id)
    hass.config_entries.async_update_entry(config_entry, options={**config_entry.options, CONF_ALLOW_PERMANENT_DELETE: True})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await refused(hass, config_entry, "delete_permanently", refs=[ref(1)]) == "not_in_trash_or_junk"
    empty = await call(hass, config_entry, "delete_permanently", refs=[tref(8)])
    assert empty["data"]["preview"]["count"] == 0 and "confirm_token" not in empty["data"]


async def test_codes_die_with_a_reload(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Codes live only in memory: after a reload (or restart) a new preview is needed."""
    await _setup(hass, config_entry)
    code = (await call(hass, config_entry, "delete_permanently", refs=[tref(1)]))["data"]["confirm_token"]
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await refused(hass, config_entry, "delete_permanently", refs=[tref(1)], confirm_token=code) == "confirm_token_invalid"
    assert 1 in mailbox.mailboxes["Gelöscht"].messages
    log = (await call(hass, config_entry, "get_audit_log"))["data"]["entries"]
    assert [e["outcome"] for e in log] == ["rejected", "preview"]  # the preview entry survived the reload (flushed)


async def test_unload_writes_pending_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, hass_storage: dict[str, Any]
) -> None:
    """Counters, audit log and the last seen UID are written on unload, not only after their save delay."""
    await _setup(hass, config_entry)
    await call(hass, config_entry, "trash", refs=[ref(1)])
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    prefix = f"{DOMAIN}.{config_entry.entry_id}"
    assert hass_storage[f"{prefix}.actions"]["data"]["deleted"] == 1
    assert hass_storage[f"{prefix}.audit"]["data"]["entries"][-1]["action"] == "trash"
    assert hass_storage[f"{prefix}.state"]["data"]["last_uid"] == 3


async def test_daily_limit_counts_trash_and_permanent(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Every message moved to the trash or deleted for good counts; the limit survives a reload."""
    await _setup(hass, config_entry, **{CONF_MAX_DELETE_PER_DAY: 3})
    await call(hass, config_entry, "trash", refs=[ref(1), ref(2)])
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, config_entry, "trash", refs=[ref(3), ref(9)])
    assert err.value.translation_key == "daily_delete_limit"
    assert err.value.translation_placeholders == {"limit": "3", "remaining": "1"}
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    first = await call(hass, config_entry, "delete_permanently", refs=[tref(1)])
    await call(hass, config_entry, "delete_permanently", refs=[tref(1)], confirm_token=first["data"]["confirm_token"])
    assert await refused(hass, config_entry, "trash", refs=[ref(3)]) == "daily_delete_limit"


async def test_too_many_refs_per_call(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """At most 50 messages per deleting call (schema)."""
    await _setup(hass, config_entry)
    with pytest.raises(Exception, match="refs"):
        await call(hass, config_entry, "trash", refs=[ref(uid) for uid in range(1, 52)])


async def test_delete_folder_two_steps(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Preview + code; protected folders and non-empty folders are refused."""
    await _setup(hass, config_entry)
    assert await refused(hass, config_entry, "delete_folder", path="Gelöscht") == "protected_folder_delete"
    mailbox.mailboxes["Archiv"].messages[1] = plain_message()
    assert await refused(hass, config_entry, "delete_folder", path="Archiv") == "folder_not_empty"
    del mailbox.mailboxes["Archiv"].messages[1]
    first = await call(hass, config_entry, "delete_folder", path="Archiv")
    assert first["data"]["preview"] == {"path": "Archiv", "messages": 0}
    assert await refused(hass, config_entry, "delete_folder", path="Gesendet", confirm_token=first["data"]["confirm_token"]) == (
        "confirm_token_invalid"
    )
    second = await call(hass, config_entry, "delete_folder", path="Archiv")
    done = await call(hass, config_entry, "delete_folder", path="Archiv", confirm_token=second["data"]["confirm_token"])
    assert done["data"] == {"deleted_folder": "Archiv"}
    assert _cmds(mailbox, "delete_folder") == [("delete_folder", "Archiv")]


async def test_permanent_delete_is_never_repeated(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """A dropped connection during deletion: outcome unknown, audited, not retried."""
    await _setup(hass, config_entry)
    code = (await call(hass, config_entry, "delete_permanently", refs=[tref(1)]))["data"]["confirm_token"]
    mailbox.write_error = IMAPClientAbortError("socket error: EOF")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, config_entry, "delete_permanently", refs=[tref(1)], confirm_token=code)
    assert err.value.translation_key == "outcome_unknown"
    assert len(_cmds(mailbox, "add_flags")) == 0  # the failing command raised before anything was flagged
    log = (await call(hass, config_entry, "get_audit_log"))["data"]["entries"]
    assert log[0]["action"] == "delete_permanently" and log[0]["outcome"] == "unknown"
