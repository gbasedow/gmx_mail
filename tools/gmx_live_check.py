#!/usr/bin/env python3
"""Live check of the integration's own IMAP code against a real GMX test account (manual, never in CI).

Answers the open M4 questions (SPEC section 7) with the code that Home Assistant will run:
  * verified TLS connect, capabilities, special folders;
  * effect of a read-write SELECT (used by the IDLE connection) on ``\\Recent``;
  * partial FETCH (``BODY.PEEK[1]<0.n>``): parsed directly or only via the fallback (GreenMail breaks it);
  * MOVE/COPY: new references from COPYUID or only via the Message-ID fallback;
  * draft APPEND to Entwürfe with APPENDUID;
  * IDLE: notification for a new message and - with ``--idle-minutes 35`` - GMX's ``* BYE timeout`` and the reconnect.

Safety rules (same as tools/gmx_probe.py):
  * credentials only from the environment (GMX_USER, GMX_PASS); prefer an app password of a test account;
  * works only in its own new test folder (aborts if it exists) and deletes exactly the messages it created;
  * prints metadata only: no addresses, subjects or content.

Usage:
    export GMX_USER='testkonto@gmx.de' GMX_PASS='<app password>'
    python3 tools/gmx_live_check.py --idle-minutes 2 > live_check.json
    python3 tools/gmx_live_check.py --idle-minutes 35 > live_check_idle.json      # observe BYE + reconnect

Needs: Python 3.14 (same as Home Assistant 2026.11; the integration code uses 3.14 syntax) and
``pip install imapclient aioimaplib certifi`` (no Home Assistant installation required).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import ssl
import sys
import time
import types
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components" / "gmx_mail"


def _load_client() -> types.SimpleNamespace:
    """Import the integration's client modules without importing Home Assistant (the package __init__ needs HA)."""
    for name, path in (("custom_components", ROOT / "custom_components"), ("custom_components.gmx_mail", PACKAGE)):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module
    names = ("const", "client", "client.errors", "client.folders", "client.sanitize", "client.structure", "client.imap")
    names += ("client.reader", "client.writer", "client.idle")
    loaded = {}
    for short in names:
        full = f"custom_components.gmx_mail.{short}"
        file = PACKAGE / (short.replace(".", "/") + ("/__init__.py" if short == "client" else ".py"))
        spec = importlib.util.spec_from_file_location(
            full, file, submodule_search_locations=[str(file.parent)] if short == "client" else None
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
        loaded[short.replace("client.", "")] = module
    return types.SimpleNamespace(**loaded)


def _context(cafile: str | None, imap: Any) -> ssl.SSLContext:
    if cafile:
        return imap.tls_context_with_minimum(ssl.create_default_context(cafile=cafile))
    try:
        import certifi

        return imap.tls_context_with_minimum(ssl.create_default_context(cafile=certifi.where()))
    except ImportError:
        return imap.tls_context_with_minimum(ssl.create_default_context())


def _message(user: str, marker: str, index: int) -> bytes:
    return (
        f"From: {user}\r\nTo: {user}\r\nSubject: gmx_mail live check {index}\r\n"
        f"Message-ID: <{marker}-{index}@gmx-mail-live-check>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        + "Testnachricht der Integration gmx_mail (Live-Check). " * 20
        + "\r\n"
    ).encode()


def run_checks(args: argparse.Namespace, user: str, password: str) -> dict[str, Any]:
    """Run all checks and return a JSON-serialisable report (metadata only)."""
    c = _load_client()
    settings = c.imap.ImapSettings(host=args.host, port=args.port, security=c.const.Security.SSL, username=user, password=password)
    report: dict[str, Any] = {"tool": "gmx_live_check", "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    context = _context(args.cafile, c.imap)
    session = c.imap.open_imap_connection(settings, context)
    marker = uuid.uuid4().hex[:10]
    test_folder = f"{args.folder_prefix}-{marker}"
    created_drafts: list[int] = []
    try:
        info = c.imap.probe_server(session)
        folder_map = c.folders.map_special_folders(info.folders)
        report["capabilities"] = sorted(info.capabilities)
        report["special_folders"] = folder_map.as_dict()
        if any(f.name == test_folder for f in info.folders):
            raise SystemExit("test folder exists - aborting")
        session.create_folder(test_folder)
        target = f"{test_folder}{next((f.delimiter for f in info.folders if f.delimiter), '/')}Ziel"
        session.create_folder(target)
        for index in range(3):
            session.append(test_folder, _message(user, marker, index))
        report["recent_after_select"] = _recent_check(c, settings, context, session, test_folder)
        session.select_folder(test_folder, readonly=True)
        uids = sorted(session.search(["ALL"]))
        validity = session.folder_status(test_folder, ["UIDVALIDITY"])[b"UIDVALIDITY"]
        # Partial FETCH exactly as the reader does it (GreenMail answers it in a form imapclient cannot parse).
        partial = c.reader._fetch_partial(session, uids[:1], "1", 64)
        report["partial_fetch"] = "parsed" if partial is not None else "fallback_needed"
        session.unselect_folder()
        refs = [c.reader.MailRef(test_folder, uid, int(validity)) for uid in uids]

        copied = c.writer.copy_or_move(session, info, refs[:1], target, move=False)
        report["copy"] = {"count": copied.count, "new_refs_known": copied.new_refs[0] is not None, "warnings": copied.warnings}
        store = c.writer._response_store(session)
        moved = c.writer.copy_or_move(session, info, refs[1:2], target, move=True)
        report["move"] = {
            "count": moved.count,
            "new_refs_known": moved.new_refs[0] is not None,
            "via_copyuid": not moved.warnings and bool(store is not None and store.get("COPYUID")),
            "warnings": moved.warnings,
        }
        flags = c.writer.set_flags(session, info, refs[2:3], ["flagged", "$GmxMailLiveCheck"], [])
        report["set_flags"] = {"count": flags.count}

        draft = c.writer.DraftInput(sender=user, to=[user], subject="gmx_mail live check draft", body_text="Entwurf (Live-Check)")
        ref, size = c.writer.save_draft(session, info, folder_map, draft)
        report["draft"] = {
            "ref_known": ref is not None,
            "size": size,
            "folder_role_ok": bool(ref and ref["folder"] == folder_map.get(c.const.FolderRole.DRAFTS)),
        }
        if ref:
            created_drafts.append(int(ref["uid"]))

        report["idle"] = asyncio.run(_idle_check(c, settings, context, user, password, marker, args.idle_minutes, args.host, args.port))
    finally:
        _cleanup(session, c, folder_map if "folder_map" in locals() else None, test_folder, created_drafts, report)
        session.logout()
    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return report


def _recent_check(c: Any, settings: Any, context: ssl.SSLContext, session: Any, folder: str) -> dict[str, Any]:
    """Effect of a read-write SELECT (as the IDLE connection does it) on ``\\Recent``, seen from a second connection."""
    observer = c.imap.open_imap_connection(settings, context)
    try:
        before = observer.folder_status(folder, [b"RECENT"]).get(b"RECENT")
        selected = session.select_folder(folder, readonly=False).get(b"RECENT")
        session.unselect_folder()  # never CLOSE after a read-write SELECT
        after = observer.folder_status(folder, [b"RECENT"]).get(b"RECENT")
    finally:
        observer.logout()
    return {"status_before": before, "on_select": selected, "status_after": after, "cleared_by_select": bool(before) and not after}


async def _idle_check(
    c: Any, settings: Any, context: ssl.SSLContext, user: str, password: str, marker: str, minutes: float, host: str, port: int
) -> dict[str, Any]:
    """IDLE on the INBOX: notification for an appended message, then wait ``minutes`` and record BYE/reconnects."""
    changes: list[float] = []
    states: list[tuple[float, str]] = []
    start = time.monotonic()

    async def _ssl() -> ssl.SSLContext:
        return context

    watcher = c.idle.IdleWatcher(
        settings,
        ssl_context=_ssl,
        limiter=c.imap.ConnectionLimiter(),
        on_change=lambda: changes.append(time.monotonic() - start),
        on_auth_failed=lambda: None,
        renew_seconds=24 * 60,
        on_state_change=lambda: states.append((round(time.monotonic() - start, 1), watcher.state.value)),
    )
    task = asyncio.create_task(watcher.async_run())
    result: dict[str, Any] = {}
    try:
        for _ in range(200):
            if watcher.state.value == "connected":
                break
            await asyncio.sleep(0.1)
        result["connected"] = watcher.state.value == "connected"
        before = len(changes)
        appended_at = time.monotonic() - start

        def _append() -> None:
            helper = c.imap.open_imap_connection(c.imap.ImapSettings(host, port, c.const.Security.SSL, user, password), context)
            helper.append("INBOX", _message(user, marker, 99))
            helper.logout()

        await asyncio.to_thread(_append)
        for _ in range(300):
            if len(changes) > before:
                break
            await asyncio.sleep(0.1)
        result["push_delay_s"] = round(changes[before] - appended_at, 1) if len(changes) > before else None
        await asyncio.sleep(max(0.0, minutes * 60 - (time.monotonic() - start)))
        result["reconnects"] = watcher.reconnects
        result["state_changes"] = states
        result["minutes"] = minutes
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return result


def _cleanup(session: Any, c: Any, folder_map: Any, test_folder: str, drafts: list[int], report: dict[str, Any]) -> None:
    """Delete only what this run created: its test folders, its drafts and its INBOX message (by Message-ID)."""
    errors: list[str] = []
    try:
        for folder in sorted(
            {f.name for f in c.folders.parse_folder_list(session.list_folders()) if f.name.startswith(test_folder)}, reverse=True
        ):
            session.delete_folder(folder)
    except Exception as err:  # noqa: BLE001 - report, never raise during cleanup
        errors.append(f"folders: {type(err).__name__}")
    drafts_folder = folder_map.get(c.const.FolderRole.DRAFTS) if folder_map else None
    for folder, uids in ((drafts_folder, drafts), ("INBOX", None)):
        if not folder:
            continue
        try:
            session.select_folder(folder)
            own = uids if uids is not None else session.search(["HEADER", "Message-ID", "gmx-mail-live-check"])
            if own:
                session.add_flags(own, [b"\\Deleted"], silent=True)
                session.uid_expunge(own)  # exactly these UIDs (UIDPLUS), never a mailbox-wide EXPUNGE
            session.unselect_folder()
        except Exception as err:  # noqa: BLE001 - report, never raise during cleanup
            errors.append(f"{'drafts' if folder != 'INBOX' else 'inbox'}: {type(err).__name__}")
    report["cleanup"] = {"ok": not errors, "errors": errors}


def main() -> int:
    """Entry point."""
    if sys.version_info < (3, 14):  # noqa: UP036 - the tool runs outside HA, often on an older Python
        print(f"Python 3.14 or newer is required (found {sys.version.split()[0]}); the integration code uses 3.14 syntax", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--idle-minutes", type=float, default=2.0, help="how long to keep IDLE open (35 to observe GMX's BYE)")
    parser.add_argument("--host", default="imap.gmx.net")
    parser.add_argument("--port", type=int, default=993)
    parser.add_argument("--cafile", help="CA file (only for local test servers)")
    parser.add_argument("--folder-prefix", default="HA-Live")
    args = parser.parse_args()
    user, password = os.environ.get("GMX_USER"), os.environ.get("GMX_PASS")
    if not user or not password:
        print("GMX_USER and GMX_PASS must be set (use an app password of a test account)", file=sys.stderr)
        return 2
    try:
        report = run_checks(args, user, password)
    except Exception as err:
        hints = {
            "GmxAuthError": "login rejected: check the address, the (app) password and that POP3/IMAP is enabled in the GMX webmailer",
            "GmxTlsError": "TLS handshake failed (certificate or TLS version)",
            "GmxConnectionError": "server not reachable (host, port, firewall or network)",
        }
        if type(err).__name__ not in hints:
            raise  # unexpected: keep the traceback (it contains no credentials)
        print(f"{type(err).__name__}: {hints[type(err).__name__]}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
