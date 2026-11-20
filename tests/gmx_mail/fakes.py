"""In-memory fakes for imapclient and smtplib (no sockets)."""

from __future__ import annotations

import base64
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from imapclient.exceptions import IMAPClientAbortError, IMAPClientError, LoginError, ProtocolError

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
    mailboxes: dict[str, FakeMailbox] = field(default_factory=dict)
    # Optional hook: (folder, criteria, charset) -> UIDs; default: all non-deleted messages.
    search_hook: Any = None
    search_error: BaseException | None = None
    broken_partial_fetch: bool = False
    commands: list[tuple[Any, ...]] = field(default_factory=list)

    def mailbox(self, name: str, uidvalidity: int = 1234) -> FakeMailbox:
        """Return (and create) a mailbox."""
        return self.mailboxes.setdefault(name, FakeMailbox(uidvalidity=uidvalidity))

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
        self.selected: str | None = None

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

    # ------------------------------------------------------------------ mailbox commands

    def select_folder(self, folder: str, readonly: bool = False) -> dict[bytes, Any]:
        """SELECT/EXAMINE."""
        self._check_open()
        self.server.commands.append(("select", folder, readonly))
        box = self.server.mailboxes.get(folder)
        if box is None:
            raise IMAPClientError("select failed: NO [NONEXISTENT] Unknown folder")
        self.selected = folder
        return {b"EXISTS": len(box.messages), b"UIDVALIDITY": box.uidvalidity, b"READ-WRITE": not readonly}

    def unselect_folder(self) -> bytes:
        """UNSELECT."""
        self._check_open()
        if b"UNSELECT" not in self.server.capabilities:
            raise IMAPClientError("UNSELECT not supported")
        self.server.commands.append(("unselect",))
        self.selected = None
        return b"OK"

    def folder_status(self, folder: str, what: Any = None) -> dict[bytes, Any]:
        """STATUS."""
        self._check_open()
        box = self.server.mailboxes.get(folder)
        if box is None:
            raise IMAPClientError("status failed: NO")
        unseen = sum(1 for m in box.messages.values() if b"\\Seen" not in m.flags)
        return {b"MESSAGES": len(box.messages), b"UNSEEN": unseen}

    def _box(self) -> FakeMailbox:
        assert self.selected is not None, "no folder selected"
        return self.server.mailboxes[self.selected]

    def search(self, criteria: Any = "ALL", charset: str | None = None) -> list[int]:
        """UID SEARCH."""
        self._check_open()
        self.server.commands.append(("search", list(criteria), charset))
        if self.server.search_error is not None:
            raise self.server.search_error
        if self.server.search_hook is not None:
            return list(self.server.search_hook(self.selected, list(criteria), charset))
        return [uid for uid, m in self._box().messages.items() if b"\\Deleted" not in m.flags]

    def sort(self, sort_criteria: Any, criteria: Any = "ALL", charset: str = "UTF-8") -> list[int]:
        """UID SORT by INTERNALDATE (good enough for tests)."""
        self._check_open()
        self.server.commands.append(("sort", list(sort_criteria), list(criteria), charset))
        if self.server.search_error is not None:
            raise self.server.search_error
        if self.server.search_hook is not None:
            uids = list(self.server.search_hook(self.selected, list(criteria), charset))
        else:
            uids = [uid for uid, m in self._box().messages.items() if b"\\Deleted" not in m.flags]
        box = self._box()
        return sorted(uids, key=lambda u: (box.messages[u].internaldate, u), reverse="REVERSE" in sort_criteria)

    def fetch(self, messages: Any, data: Any, modifiers: Any = None) -> dict[int, dict[bytes, Any]]:
        """UID FETCH of the items the integration uses."""
        self._check_open()
        items = list(data)
        self.server.commands.append(("fetch", list(messages), items))
        box = self._box()
        result: dict[int, dict[bytes, Any]] = {}
        for uid in messages:
            msg = box.messages.get(uid)
            if msg is None:
                continue
            entry: dict[bytes, Any] = {b"SEQ": uid}
            for item in items:
                entry.update(msg.fetch_item(item, broken_partial=self.server.broken_partial_fetch))
            result[uid] = entry
        return result


_PEEK_RE = re.compile(r"^BODY\.PEEK\[([^\]]*)\](?:<0\.(\d+)>)?$")


@dataclass
class FakeMessage:
    """A stored message: header block, raw (transfer-encoded) parts and the BODYSTRUCTURE."""

    header: bytes
    parts: dict[str, bytes]
    bodystructure: Any
    internaldate: datetime = field(default_factory=lambda: datetime(2026, 11, 20, 10, 0, tzinfo=UTC))
    flags: tuple[bytes, ...] = ()
    size: int = 0

    def fetch_item(self, item: str, *, broken_partial: bool) -> dict[bytes, Any]:
        """Return the response entries for one FETCH item."""
        upper = item.upper()
        if upper == "FLAGS":
            return {b"FLAGS": self.flags}
        if upper == "RFC822.SIZE":
            return {b"RFC822.SIZE": self.size or len(self.header) + sum(len(p) for p in self.parts.values())}
        if upper == "INTERNALDATE":
            return {b"INTERNALDATE": self.internaldate}
        if upper == "BODYSTRUCTURE":
            return {b"BODYSTRUCTURE": self.bodystructure}
        match = _PEEK_RE.match(item)
        assert match, f"unexpected FETCH item {item!r} (reads must use BODY.PEEK)"
        section, partial = match.group(1), match.group(2)
        if section.upper().startswith("HEADER"):
            return {f"BODY[{section}]".encode(): self.header}
        content = self.parts.get(section)
        if content is None:
            return {}
        if partial is not None:
            if broken_partial:
                raise ProtocolError("uneven number of response items")
            return {f"BODY[{section}]<0>".encode(): content[: int(partial)]}
        return {f"BODY[{section}]".encode(): content}


@dataclass
class FakeMailbox:
    """A folder with messages by UID."""

    uidvalidity: int = 1234
    messages: dict[int, FakeMessage] = field(default_factory=dict)


def plain_message(
    *,
    subject: str = "Hallo",
    sender: str = "Anna Beispiel <anna@example.org>",
    to: str = "user@gmx.test",
    body: str = "Hallo Welt",
    date: datetime | None = None,
    extra_headers: str = "",
    flags: tuple[bytes, ...] = (),
) -> FakeMessage:
    """A single-part text/plain message (base64-encoded body, part '1')."""
    from email.header import Header  # noqa: PLC0415

    header = (
        f"From: {sender}\r\nTo: {to}\r\nSubject: {Header(subject, 'utf-8').encode()}\r\n"
        f"Date: Fri, 20 Nov 2026 10:00:00 +0100\r\nMessage-ID: <m1@example.org>\r\n{extra_headers}\r\n"
    ).encode()
    raw = base64.encodebytes(body.encode("utf-8"))
    structure = (b"text", b"plain", (b"charset", b"utf-8"), None, None, b"base64", len(raw), 1, None, None, None)
    message = FakeMessage(header=header, parts={"1": raw}, bodystructure=structure, flags=flags)
    if date is not None:
        message.internaldate = date
    return message


def mixed_message(*, html: str, pdf: bytes = b"%PDF-1.4 test", filename_param: bytes = b"utf-8''Rechnung%20M%C3%A4rz.pdf") -> FakeMessage:
    """multipart/mixed with alternative (plain 1.1 + html 1.2) and a PDF attachment (part 2)."""
    header = b"From: shop@example.com\r\nTo: user@gmx.test\r\nSubject: Rechnung\r\nReply-To: other@evil.example\r\n\r\n"
    plain_raw = b"Plain body"
    html_raw = html.encode()
    pdf_raw = base64.encodebytes(pdf)
    structure = (
        [
            (
                [
                    (b"text", b"plain", (b"charset", b"utf-8"), None, None, b"7bit", len(plain_raw), 1, None, None, None),
                    (b"text", b"html", (b"charset", b"utf-8"), None, None, b"7bit", len(html_raw), 1, None, None, None),
                ],
                b"alternative",
                (b"boundary", b"b1"),
                None,
                None,
            ),
            (
                b"APPLICATION",
                b"PDF",
                None,
                None,
                None,
                b"base64",
                len(pdf_raw),
                None,
                (b"attachment", (b"filename*", filename_param)),
                None,
            ),
        ],
        b"mixed",
        (b"boundary", b"b0"),
        None,
        None,
    )
    return FakeMessage(header=header, parts={"1.1": plain_raw, "1.2": html_raw, "2": pdf_raw}, bodystructure=structure)


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
