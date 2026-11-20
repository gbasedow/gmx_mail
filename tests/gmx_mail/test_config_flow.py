"""Tests for the config, reauth, reconfigure and options flows."""

from __future__ import annotations

import ssl
from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from imapclient.exceptions import IMAPClientAbortError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gmx_mail.const import (
    CONF_ALLOWLIST,
    CONF_EMAIL,
    CONF_FOLDER_SENT,
    CONF_IMAP_HOST,
    CONF_IMAP_PORT,
    CONF_IMAP_SECURITY,
    CONF_PASSWORD,
    CONF_PERMISSIONS,
    CONF_SEND_MODE,
    CONF_SMTP_HOST,
    CONF_SMTP_PORT,
    CONF_SMTP_SECURITY,
    DEFAULT_OPTIONS,
    DOMAIN,
    SECTION_FOLDERS,
    SECTION_PERMISSIONS,
    SECTION_SENDING,
    SECTION_SERVER,
    FolderRole,
)
from custom_components.gmx_mail.options import nest

from .conftest import ENTRY_DATA
from .fakes import EMAIL, PASSWORD, FakeImapServer, FakeSmtpServer

SERVER_INPUT: dict[str, Any] = {
    CONF_IMAP_HOST: "imap.gmx.net",
    CONF_IMAP_PORT: 993,
    CONF_IMAP_SECURITY: "ssl",
    CONF_SMTP_HOST: "mail.gmx.net",
    CONF_SMTP_PORT: 587,
    CONF_SMTP_SECURITY: "starttls",
}


def user_input(email: str = EMAIL, password: str = PASSWORD, **server: Any) -> dict[str, Any]:
    """Build the user step input."""
    return {CONF_EMAIL: email, CONF_PASSWORD: password, SECTION_SERVER: {**SERVER_INPUT, **server}}


async def test_user_flow_creates_entry(hass: HomeAssistant, imap_server: FakeImapServer, smtp_server: FakeSmtpServer) -> None:
    """Happy path: IMAP and SMTP are checked, defaults are restrictive."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input(email=f"  {EMAIL.upper()} "))
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL.upper()
    assert result["data"] == {**ENTRY_DATA, CONF_EMAIL: EMAIL.upper()}
    assert result["options"] == DEFAULT_OPTIONS
    assert result["result"].unique_id == EMAIL.lower()
    # Config-flow check (logged out) + setup connection.
    assert imap_server.clients[0].logged_out
    assert smtp_server.sessions[0].quit_called


async def test_user_flow_normalises_server_input(hass: HomeAssistant, imap_server: FakeImapServer, smtp_server: FakeSmtpServer) -> None:
    """Hosts are trimmed and lower-cased, ports become int; STARTTLS for IMAP works."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input(imap_host=" IMAP.GMX.NET ", imap_port=143.0, imap_security="starttls"),
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_IMAP_HOST] == "imap.gmx.net"
    assert result["data"][CONF_IMAP_PORT] == 143
    assert imap_server.clients[0].port == 143
    assert not imap_server.clients[0].ssl and imap_server.clients[0].tls


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        (lambda imap, smtp: setattr(imap, "password", "other"), "invalid_auth_or_imap_disabled"),
        (lambda imap, smtp: setattr(smtp, "password", "other"), "smtp_auth_failed"),
        (lambda imap, smtp: setattr(imap, "connect_error", ConnectionRefusedError()), "cannot_connect"),
        (lambda imap, smtp: setattr(imap, "list_error", IMAPClientAbortError("EOF")), "cannot_connect"),
        (lambda imap, smtp: setattr(smtp, "connect_error", TimeoutError()), "cannot_connect"),
        (lambda imap, smtp: setattr(imap, "connect_error", ssl.SSLError("bad")), "tls_error"),
        (lambda imap, smtp: setattr(smtp, "starttls_offered", False), "tls_error"),
        (lambda imap, smtp: setattr(imap, "connect_error", RuntimeError("bug")), "unknown"),
    ],
)
async def test_user_flow_errors_and_recovery(
    hass: HomeAssistant, imap_server: FakeImapServer, smtp_server: FakeSmtpServer, setup: Any, error: str
) -> None:
    """Every failure shows its error key; after fixing it the flow completes."""
    pristine = (imap_server.password, smtp_server.password)
    setup(imap_server, smtp_server)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input())
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    imap_server.password, smtp_server.password = pristine
    imap_server.connect_error = imap_server.list_error = smtp_server.connect_error = None
    smtp_server.starttls_offered = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input())
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_invalid_email(hass: HomeAssistant, imap_server: FakeImapServer, smtp_server: FakeSmtpServer) -> None:
    """An address without '@' is rejected before any connection."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input(email="user.gmx.de"))
    assert result["errors"] == {CONF_EMAIL: "invalid_email"}
    assert not imap_server.clients


async def test_user_flow_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """The same address (any case) can only be added once."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input(email=EMAIL.upper()))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not imap_server.clients


async def test_reauth_flow(
    hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """A new app password is validated, stored and the entry reloaded."""
    config_entry.add_to_hass(hass)
    imap_server.password = smtp_server.password = "new-app-password"
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["account"] == EMAIL

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "still-wrong"})
    assert result["errors"] == {"base": "invalid_auth_or_imap_disabled"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new-app-password"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-app-password"
    assert config_entry.data[CONF_EMAIL] == EMAIL


async def test_reconfigure_flow(
    hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """Server settings can be changed; they are checked with the stored credentials."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    imap_server.connect_error = ConnectionRefusedError()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SERVER_INPUT, CONF_SMTP_PORT: 465, CONF_SMTP_SECURITY: "ssl"}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    imap_server.connect_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SERVER_INPUT, CONF_SMTP_PORT: 465, CONF_SMTP_SECURITY: "ssl"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_SMTP_PORT] == 465
    assert config_entry.data[CONF_SMTP_SECURITY] == "ssl"
    assert config_entry.data[CONF_PASSWORD] == PASSWORD


async def test_options_flow(
    hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """Options are validated, flattened and stored; the folder list feeds the overrides."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema  # type: ignore[union-attr]
    folders_section = next(value for key, value in schema.items() if key == SECTION_FOLDERS)
    folder_field = next(value for key, value in folders_section.schema.schema.items() if key == CONF_FOLDER_SENT)
    assert "Gesendet" in folder_field.config["options"]

    form = nest(DEFAULT_OPTIONS)
    form[SECTION_SENDING] = {**form[SECTION_SENDING], CONF_SEND_MODE: "any", CONF_ALLOWLIST: ["a@b.de"]}
    result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "any_requires_wildcard"}

    form[SECTION_SENDING] = {**form[SECTION_SENDING], CONF_SEND_MODE: "allowlist", CONF_ALLOWLIST: ["A@B.de", "*@firma.de"]}
    form[SECTION_PERMISSIONS] = {**form[SECTION_PERMISSIONS], CONF_PERMISSIONS: ["read", "send"]}
    form[SECTION_FOLDERS] = {CONF_FOLDER_SENT: "Archiv"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_ALLOWLIST] == ["a@b.de", "*@firma.de"]
    assert config_entry.options[CONF_PERMISSIONS] == ["read", "send"]
    assert config_entry.options[CONF_FOLDER_SENT] == "Archiv"
    assert set(config_entry.options) == set(DEFAULT_OPTIONS)

    # The update listener reloaded the entry with the new folder override.
    assert config_entry.runtime_data.folder_map.get(FolderRole.SENT) == "Archiv"


async def test_options_flow_without_loaded_entry(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Without a connection the folder overrides are free text."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], nest(DEFAULT_OPTIONS))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(config_entry.options) == DEFAULT_OPTIONS
