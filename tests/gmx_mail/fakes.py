"""In-memory fakes for imapclient and smtplib (no sockets)."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass, field
from typing import Any

from imapclient.exceptions import IMAPClientAbortError, LoginError

# Capabilities after login as measured against GMX in M0 (docs/probe-2026-11-20-v4.json).
GMX_CAPABILITIES = (
    b"IMAP4REV1 APPENDLIMIT=29360128 CHILDREN ENABLE ID IDLE LIST-EXTENDED LIST-STATUS LITERAL- MOVE NAMESPACE "
    b"SASL-IR SORT SPECIAL-USE THREAD=ORDEREDSUBJECT UIDPLUS UNSELECT WITHIN"
).split()

# Folder layout as found in M0 (GMX-NOTES row 14); "Archiv" stands in for a user folder.
GMX_FOLDERS: list[tuple[tuple[bytes, ...], bytes, str]] = [
    ((b"\\HasNoChildren",), b"/", "INBOX"),
    ((b"\\Drafts", b"\\NoInferiors"), b"/", "Entwürfe"),
    ((b"\\Trash", b"\\HasNoChildren"), b"/", "Gelöscht"),
    ((b"\\Sent", b"\\NoInferiors"), b"/", "Gesendet"),
    ((b"\\Junk", b"\\NoInferiors"), b"/", "Spamverdacht"),
    ((b"\\HasNoChildren",), b"/", "Archiv"),
]

PASSWORD = "app-password-for-tests"
EMAIL = "user@gmx.test"


@dataclass
class FakeImapServer:
    """Shared state of all fake IMAP connections."""

    password: str = PASSWORD
    capabilities: tuple[bytes, ...] = tuple(GMX_CAPABILITIES)
    folders: list[tuple[tuple[bytes, ...], bytes, str]] = field(default_factory=lambda: list(GMX_FOLDERS))
    connect_error: BaseException | None = None
    starttls_error: BaseException | None = None
    list_error: BaseException | None = None
    noop_error: BaseException | None = None
    login_error: BaseException | None = None
    logout_error: BaseException | None = None
    clients: list[FakeIMAPClient] = field(default_factory=list)
    logins: int = 0

    def __call__(
        self, host: str, port: int | None = None, ssl: bool = True, ssl_context: Any = None, timeout: Any = None
    ) -> FakeIMAPClient:
        """Act as the IMAPClient class."""
        if self.connect_error is not None:
            raise self.connect_error
        client = FakeIMAPClient(self, host=host, port=port, ssl=ssl, ssl_context=ssl_context)
        self.clients.append(client)
        return client

    @property
    def open_clients(self) -> list[FakeIMAPClient]:
        """Return connections that are still open."""
        return [c for c in self.clients if not c.closed]


class FakeIMAPClient:
    """Implements the subset of imapclient.IMAPClient used by the integration."""

    def __init__(self, server: FakeImapServer, *, host: str, port: int | None, ssl: bool, ssl_context: Any) -> None:
        """Initialise."""
        self.server = server
        self.host = host
        self.port = port
        self.ssl = ssl
        self.ssl_context = ssl_context
        self.tls = ssl
        self.logged_in = False
        self.closed = False
        self.logged_out = False
        self.noops = 0
        self.commands: list[str] = []

    def starttls(self, ssl_context: Any = None) -> bytes:
        """Upgrade to TLS."""
        if self.server.starttls_error is not None:
            raise self.server.starttls_error
        self.tls = True
        return b"OK"

    def login(self, username: str, password: str) -> bytes:
        """Log in."""
        assert self.tls, "credentials must never be sent without TLS"
        if self.server.login_error is not None:
            raise self.server.login_error
        if password != self.server.password:
            raise LoginError("[AUTHENTICATIONFAILED] Authentication failed")
        self.logged_in = True
        self.server.logins += 1
        return b"OK"

    def capabilities(self) -> tuple[bytes, ...]:
        """Return capabilities."""
        return self.server.capabilities

    def list_folders(self, directory: str = "", pattern: str = "*") -> list[tuple[tuple[bytes, ...], bytes, str]]:
        """Return folders."""
        self._check_open()
        if self.server.list_error is not None:
            raise self.server.list_error
        return list(self.server.folders)

    def noop(self) -> tuple[bytes, list[Any]]:
        """NOOP."""
        self._check_open()
        self.noops += 1
        if self.server.noop_error is not None:
            raise self.server.noop_error
        return b"NOOP completed", []

    def logout(self) -> bytes:
        """Log out."""
        if self.server.logout_error is not None:
            raise self.server.logout_error
        self.logged_out = True
        self.closed = True
        return b"BYE"

    def shutdown(self) -> None:
        """Close the socket."""
        self.closed = True

    def _check_open(self) -> None:
        if self.closed:
            raise IMAPClientAbortError("socket error: EOF")


@dataclass
class FakeSmtpServer:
    """Shared state of fake SMTP connections."""

    password: str = PASSWORD
    starttls_offered: bool = True
    size: str = "141557760"
    connect_error: BaseException | None = None
    sessions: list[FakeSMTP] = field(default_factory=list)

    def factory(self, *, implicit_tls: bool) -> Any:
        """Return a replacement for smtplib.SMTP or smtplib.SMTP_SSL."""

        def _make(host: str, port: int, timeout: float = 30, context: ssl.SSLContext | None = None) -> FakeSMTP:
            if self.connect_error is not None:
                raise self.connect_error
            session = FakeSMTP(self, tls=implicit_tls)
            self.sessions.append(session)
            return session

        return _make


class FakeSMTP:
    """Implements the subset of smtplib.SMTP used by the integration."""

    def __init__(self, server: FakeSmtpServer, *, tls: bool) -> None:
        """Initialise."""
        self.server = server
        self.tls = tls
        self.esmtp_features: dict[str, str] = {}
        self.closed = False
        self.quit_called = False

    def ehlo(self) -> tuple[int, bytes]:
        """EHLO."""
        if self.tls:
            self.esmtp_features = {"8bitmime": "", "auth": "LOGIN PLAIN", "size": self.server.size}
        else:
            self.esmtp_features = {"8bitmime": "", "size": self.server.size}
            if self.server.starttls_offered:
                self.esmtp_features["starttls"] = ""
        return 250, b"OK"

    def has_extn(self, name: str) -> bool:
        """Return whether the extension is offered."""
        return name.lower() in self.esmtp_features

    def starttls(self, context: ssl.SSLContext | None = None) -> tuple[int, bytes]:
        """Upgrade to TLS."""
        self.tls = True
        return 220, b"Ready"

    def login(self, user: str, password: str) -> tuple[int, bytes]:
        """Log in."""
        assert self.tls, "credentials must never be sent without TLS"
        if password != self.server.password:
            raise smtplib.SMTPAuthenticationError(535, b"Authentication credentials invalid")
        return 235, b"OK"

    def quit(self) -> tuple[int, bytes]:
        """QUIT."""
        self.quit_called = True
        self.closed = True
        return 221, b"Bye"

    def close(self) -> None:
        """Close."""
        self.closed = True
