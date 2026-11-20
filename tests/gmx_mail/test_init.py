"""Tests for setting up and unloading a config entry."""

from __future__ import annotations

import ssl
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.gmx_mail import issue_id
from custom_components.gmx_mail.client.errors import GmxAuthError
from custom_components.gmx_mail.client.imap import ImapSession
from custom_components.gmx_mail.const import CONF_FOLDER_TRASH, DOMAIN, ISSUE_IMAP_POSSIBLY_DISABLED, FolderRole

from .fakes import FakeImapServer


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """Setup logs in once, stores capabilities and the folder map; unload logs out."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    data = config_entry.runtime_data
    assert data.server_info.has("UIDPLUS")
    assert data.server_info.append_limit == 29360128
    assert data.folder_map.get(FolderRole.TRASH) == "Gelöscht"
    assert data.folder_map.get(FolderRole.JUNK) == "Spamverdacht"
    assert data.limiter.in_use == 1
    assert len(imap_server.open_clients) == 1

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert imap_server.clients[0].logged_out
    assert data.limiter.in_use == 0


async def test_setup_removes_stale_issue(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """A successful login clears the 'IMAP possibly disabled' repair issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(config_entry),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_IMAP_POSSIBLY_DISABLED,
    )
    await _setup(hass, config_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(config_entry)) is None


async def test_setup_auth_failure_starts_reauth(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """A rejected login starts reauth and raises the repair issue."""
    imap_server.password = "revoked"
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id(config_entry))
    assert issue is not None
    assert issue.translation_key == ISSUE_IMAP_POSSIBLY_DISABLED
    assert not imap_server.open_clients


async def test_setup_connection_failure_retries(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """Network problems lead to a setup retry, not to reauth."""
    imap_server.connect_error = TimeoutError()
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_setup_tls_failure_retries(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """Certificate problems lead to a setup retry; verification is never switched off."""
    imap_server.connect_error = ssl.SSLCertVerificationError("certificate verify failed")
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_idle_connection_is_closed_by_timer(
    hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer, freezer: FrozenDateTimeFactory
) -> None:
    """The periodic check logs the work connection out after 10 minutes without use."""
    await _setup(hass, config_entry)
    worker = config_entry.runtime_data.worker
    now = 0.0
    worker._clock = lambda: now  # deterministic monotonic clock
    worker._last_used = 0.0

    now = 11 * 60
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not worker.connected
    assert imap_server.clients[0].logged_out
    assert config_entry.runtime_data.limiter.in_use == 0


async def test_auth_failure_during_operation_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer
) -> None:
    """If GMX switches IMAP off later, the next reconnect starts reauth and creates the issue."""
    await _setup(hass, config_entry)
    worker = config_entry.runtime_data.worker
    imap_server.clients[0].closed = True
    imap_server.password = "imap-disabled"

    def read(client: ImapSession) -> int:
        return len(client.list_folders())

    with pytest.raises(GmxAuthError):
        await worker.async_run(read, readonly=True)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(config_entry)) is not None


async def test_invalid_folder_override_falls_back(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """An override that does not exist is ignored."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={**config_entry.options, CONF_FOLDER_TRASH: "Papierkorb"})
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    folder_map = config_entry.runtime_data.folder_map
    assert folder_map.get(FolderRole.TRASH) == "Gelöscht"
    assert FolderRole.TRASH in folder_map.invalid_overrides


async def test_remove_entry_deletes_issue(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """Removing the entry removes its repair issue."""
    imap_server.password = "revoked"
    await _setup(hass, config_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(config_entry)) is not None
    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(config_entry)) is None


async def test_read_survives_dropped_connection(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """End-to-end through hass' executor: a dropped connection is replaced transparently for reads."""
    await _setup(hass, config_entry)
    worker = config_entry.runtime_data.worker
    imap_server.clients[0].closed = True

    def read(client: ImapSession) -> int:
        return len(client.list_folders())

    assert await worker.async_run(read, readonly=True) == 6
    assert len(imap_server.clients) == 2
    assert config_entry.runtime_data.limiter.in_use == 1
