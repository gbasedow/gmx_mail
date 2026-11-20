"""Fixtures for the GMX Mail integration tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gmx_mail.const import (
    CONF_EMAIL,
    CONF_IMAP_HOST,
    CONF_IMAP_PORT,
    CONF_IMAP_SECURITY,
    CONF_PASSWORD,
    CONF_SMTP_HOST,
    CONF_SMTP_PORT,
    CONF_SMTP_SECURITY,
    DEFAULT_OPTIONS,
    DOMAIN,
)

from .fakes import EMAIL, PASSWORD, FakeImapServer, FakeSmtpServer

ENTRY_DATA: dict[str, Any] = {
    CONF_EMAIL: EMAIL,
    CONF_PASSWORD: PASSWORD,
    CONF_IMAP_HOST: "imap.gmx.net",
    CONF_IMAP_PORT: 993,
    CONF_IMAP_SECURITY: "ssl",
    CONF_SMTP_HOST: "mail.gmx.net",
    CONF_SMTP_PORT: 587,
    CONF_SMTP_SECURITY: "starttls",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components in every test."""


@pytest.fixture
def imap_server() -> Generator[FakeImapServer]:
    """Replace imapclient.IMAPClient by an in-memory fake."""
    server = FakeImapServer()
    with patch("custom_components.gmx_mail.client.imap.IMAPClient", server):
        yield server


@pytest.fixture
def smtp_server() -> Generator[FakeSmtpServer]:
    """Replace smtplib.SMTP / SMTP_SSL (as used by the integration) by in-memory fakes."""
    server = FakeSmtpServer()
    with (
        patch("custom_components.gmx_mail.client.smtp.smtplib.SMTP", server.factory(implicit_tls=False)),
        patch("custom_components.gmx_mail.client.smtp.smtplib.SMTP_SSL", server.factory(implicit_tls=True)),
    ):
        yield server


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the test account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL.lower(),
        data=dict(ENTRY_DATA),
        options=dict(DEFAULT_OPTIONS),
        version=1,
        minor_version=1,
    )
