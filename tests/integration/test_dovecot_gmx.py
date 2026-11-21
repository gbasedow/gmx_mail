"""Production code paths against a GMX-like Dovecot (TLS with a test CA, GMX capabilities and folder names).

Start: ``python3 tools/make_test_certs.py && docker compose up -d dovecot``, then
``pytest -p no:homeassistant -m integration tests/integration``. Skipped if the server is not reachable
(fails instead with ``REQUIRE_DOVECOT=1``, as in CI). Never a real mail account.

Unlike the GreenMail tests, these use the real connection code: implicit TLS with certificate verification,
``open_imap_connection``, the ImapWorker and the IDLE watcher with the real aioimaplib TLS client.
"""

from __future__ import annotations

import asyncio
import os
import socket
import ssl
import time
import uuid
from collections.abc import Callable, Iterator
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from imapclient import IMAPClient

from custom_components.gmx_mail.client.errors import GmxAuthError, GmxTlsError
from custom_components.gmx_mail.client.folders import map_special_folders
from custom_components.gmx_mail.client.idle import IdleWatcher
from custom_components.gmx_mail.client.imap import ConnectionLimiter, ImapSettings, ImapWorker, check_imap_account, tls_context_with_minimum
from custom_components.gmx_mail.client.reader import MailRef, SearchQuery, get_message, list_folders, poll, search
from custom_components.gmx_mail.client.smtp import SmtpSettings, check_smtp_login
from custom_components.gmx_mail.const import FolderRole, PushState, Security

pytestmark = pytest.mark.integration

HOST = os.environ.get("DOVECOT_HOST", "127.0.0.1")
IMAPS_PORT = int(os.environ.get("DOVECOT_IMAPS_PORT", "31993"))
SUBMISSION_PORT = int(os.environ.get("DOVECOT_SUBMISSION_PORT", "31587"))
USER = "user@gmx.test"
PASSWORD = "secret"
CA_FILE = Path(__file__).parent / "certs" / "ca.crt"
SETTINGS = ImapSettings(host=HOST, port=IMAPS_PORT, security=Security.SSL, username=USER, password=PASSWORD)


def _reachable() -> bool:
    try:
        with socket.create_connection((HOST, IMAPS_PORT), timeout=2):
            return CA_FILE.exists()
    except OSError:
        return False


def _context() -> ssl.SSLContext:
    return tls_context_with_minimum(ssl.create_default_context(cafile=str(CA_FILE)))


@pytest.fixture(autouse=True)
def _require_server() -> None:
    if not _reachable():
        if os.environ.get("REQUIRE_DOVECOT"):
            pytest.fail(f"Dovecot not reachable on {HOST}:{IMAPS_PORT} or test CA missing (REQUIRE_DOVECOT is set)")
        pytest.skip(f"Dovecot not reachable on {HOST}:{IMAPS_PORT} (or tools/make_test_certs.py not run)")


@pytest.fixture
def mailer() -> Iterator[IMAPClient]:
    """A TLS connection used by the tests to deliver and inspect mail."""
    client = IMAPClient(HOST, port=IMAPS_PORT, ssl=True, ssl_context=_context(), timeout=15)
    client.login(USER, PASSWORD)
    client.normalise_times = False
    yield client
    client.logout()


@pytest.fixture
def folder(mailer: IMAPClient) -> Iterator[str]:
    """A fresh folder (umlaut name, modified UTF-7 on the wire), removed afterwards."""
    name = f"IT-Prüfung-{uuid.uuid4().hex[:8]}"
    mailer.create_folder(name)
    yield name
    mailer.delete_folder(name)


def _deliver(client: IMAPClient, folder: str, subject: str, flags: tuple[bytes, ...] = ()) -> None:
    message = EmailMessage()
    message["From"] = "Jörg Müller <joerg@example.org>"
    message["To"] = USER
    message["Subject"] = subject
    message.set_content("Hallo,\nText mit Umlauten: äöü\n")
    client.append(folder, message.as_bytes(), flags=flags)


def test_tls_capabilities_and_special_folders() -> None:
    """GMX capabilities and SPECIAL-USE folders (Gesendet, Entwürfe, Gelöscht, Spamverdacht) over verified TLS."""
    info = check_imap_account(SETTINGS, _context())
    assert info.missing_recommended == []
    assert info.append_limit == 29360128
    for absent in ("CONDSTORE", "QRESYNC", "QUOTA", "ESEARCH", "UTF8=ACCEPT"):
        assert absent not in info.capabilities
    folder_map = map_special_folders(info.folders)
    assert {role: folder_map.get(role) for role in FolderRole} == {
        FolderRole.INBOX: "INBOX",
        FolderRole.SENT: "Gesendet",
        FolderRole.DRAFTS: "Entwürfe",
        FolderRole.TRASH: "Gelöscht",
        FolderRole.JUNK: "Spamverdacht",
    }
    assert not folder_map.ambiguous


def test_tls_and_login_failures_are_classified() -> None:
    """Wrong password -> auth error; unknown CA -> TLS error (verification is never switched off)."""
    wrong = ImapSettings(host=HOST, port=IMAPS_PORT, security=Security.SSL, username=USER, password="wrong")
    with pytest.raises(GmxAuthError):
        check_imap_account(wrong, _context())
    with pytest.raises(GmxTlsError):
        check_imap_account(SETTINGS, tls_context_with_minimum(ssl.create_default_context()))


def test_smtp_login_with_starttls() -> None:
    """The config-flow SMTP check works with STARTTLS (submission port)."""
    smtp = SmtpSettings(host=HOST, port=SUBMISSION_PORT, security=Security.STARTTLS, username=USER, password=PASSWORD)
    check_smtp_login(smtp, _context())


async def _executor[T](target: Callable[..., T], *args: Any) -> T:
    return await asyncio.get_running_loop().run_in_executor(None, target, *args)


async def test_worker_and_reader_with_unselect(mailer: IMAPClient, folder: str) -> None:
    """ImapWorker (real TLS connect) runs the read path; UNSELECT is available here (not with GreenMail)."""
    await asyncio.to_thread(_deliver, mailer, folder, "Rechnung Größe")
    await asyncio.to_thread(_deliver, mailer, folder, "Gelesen", (b"\\Seen",))
    worker = ImapWorker(SETTINGS, executor=_executor, ssl_context_factory=_context, limiter=ConnectionLimiter())
    try:
        info = await worker.async_connect()
        folders = await worker.async_run(
            lambda s: list_folders(s, info, map_special_folders(info.folders), include_counts=True), readonly=True
        )
        entry = next(f for f in folders if f["path"] == folder)
        assert entry["messages"] == 2 and entry["unseen"] == 1
        result = await worker.async_run(
            lambda s: search(s, info, folder, SearchQuery(subject="Größe"), order="date_desc", limit=10, offset=0, snippet=True),
            readonly=True,
        )
        assert [m["subject"] for m in result.messages] == ["Rechnung Größe"]
        assert result.messages[0]["snippet"].startswith("Hallo, Text mit Umlauten: äöü")
        ref = MailRef(folder, result.messages[0]["ref"]["uid"], result.uidvalidity)
        content = await worker.async_run(lambda s: get_message(s, info, ref, body_format="text"), readonly=True)
        assert content.text == "Hallo,\nText mit Umlauten: äöü"
    finally:
        await worker.async_shutdown()
    mailer.select_folder(folder, readonly=True)
    assert b"\\Seen" not in mailer.get_flags([ref.uid])[ref.uid]


async def test_idle_watcher_over_tls(mailer: IMAPClient, folder: str) -> None:
    """The IDLE watcher with the real aioimaplib TLS client is notified about new mail, also after a renewal."""
    changes: list[float] = []
    loop = asyncio.get_running_loop()

    async def _ssl() -> ssl.SSLContext:
        return await loop.run_in_executor(None, _context)

    watcher = IdleWatcher(
        SETTINGS,
        ssl_context=_ssl,
        limiter=ConnectionLimiter(),
        on_change=lambda: changes.append(time.monotonic()),
        on_auth_failed=lambda: None,
        renew_seconds=2,
        folder=folder,
    )
    task = asyncio.create_task(watcher.async_run())
    try:
        await _wait_for(lambda: watcher.state is PushState.CONNECTED and len(changes) == 1)
        await asyncio.to_thread(_deliver, mailer, folder, "eins")
        await _wait_for(lambda: len(changes) >= 2)
        await asyncio.sleep(3)  # at least one renewal
        before = len(changes)
        await asyncio.to_thread(_deliver, mailer, folder, "zwei")
        await _wait_for(lambda: len(changes) > before)
        assert watcher.reconnects == 0
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_poll_with_real_uidnext(mailer: IMAPClient, folder: str) -> None:
    """Baseline from UIDNEXT, then exactly the new message is reported."""
    info = check_imap_account(SETTINGS, _context())
    _deliver(mailer, folder, "alt")
    first = poll(
        mailer, info, inbox=folder, junk="Spamverdacht", last_uid=None, known_uidvalidity=None, max_events=20, include_headers=False
    )
    assert first.new_messages == [] and first.junk_messages is not None
    _deliver(mailer, folder, "neu")
    second = poll(
        mailer,
        info,
        inbox=folder,
        junk="Spamverdacht",
        last_uid=first.last_uid,
        known_uidvalidity=first.uidvalidity,
        max_events=20,
        include_headers=True,
    )
    assert [m["subject"] for m in second.new_messages] == ["neu"]


async def _wait_for(condition: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.05)
