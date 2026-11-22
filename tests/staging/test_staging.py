"""End-to-end test against a real Home Assistant (target version) in a container - the staging check.

Start (fresh state each time):
    python3 tools/make_test_certs.py
    docker compose --profile staging down -v
    docker compose up -d dovecot && docker compose --profile staging up -d --build homeassistant
    pytest -p no:homeassistant -m staging tests/staging

Uses only Home Assistant's public APIs (REST and WebSocket) - the same way ha-mcp calls the actions.
Mail server is the GMX-like Dovecot with a test CA; never a real account.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import ssl
from collections.abc import AsyncIterator
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import pytest
from imapclient import IMAPClient

from custom_components.gmx_mail.const import (
    CONF_ALLOW_PERMANENT_DELETE,
    CONF_ALLOWLIST,
    CONF_PERMISSIONS,
    CONF_SEND_MODE,
    DEFAULT_OPTIONS,
)
from custom_components.gmx_mail.options import nest

pytestmark = pytest.mark.staging

HA_URL = os.environ.get("STAGING_URL", "http://127.0.0.1:8123")
CLIENT_ID = f"{HA_URL}/"
MAIL_HOST = os.environ.get("DOVECOT_HOST", "127.0.0.1")
MAIL_PORT = int(os.environ.get("DOVECOT_IMAPS_PORT", "31993"))
USER = "user@gmx.test"
PASSWORD = "secret"
CA_FILE = Path(__file__).parent.parent / "integration" / "certs" / "ca.crt"
SERVER_IN_COMPOSE = {
    "imap_host": "dovecot",
    "imap_port": 31993,
    "imap_security": "ssl",
    "smtp_host": "dovecot",
    "smtp_port": 31587,
    "smtp_security": "starttls",
}


class Staging:
    """Small client for the Home Assistant REST and WebSocket APIs."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        """Initialise."""
        self.session = session
        self.headers = {"Authorization": f"Bearer {token}"}
        self.token = token
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._id = 0

    async def rest(self, method: str, path: str, payload: Any = None) -> Any:
        """Call the REST API and return JSON."""
        async with self.session.request(method, f"{HA_URL}{path}", json=payload, headers=self.headers) as response:
            body = await response.text()
            assert response.status < 400, f"{method} {path}: {response.status} {body[:500]}"
            return json.loads(body) if body else None

    async def ws(self) -> aiohttp.ClientWebSocketResponse:
        """Return an authenticated WebSocket."""
        if self._ws is None:
            self._ws = await self.session.ws_connect(f"{HA_URL}/api/websocket")
            assert (await self._ws.receive_json())["type"] == "auth_required"
            await self._ws.send_json({"type": "auth", "access_token": self.token})
            assert (await self._ws.receive_json())["type"] == "auth_ok"
        return self._ws

    async def command(self, payload: dict[str, Any]) -> Any:
        """Send a WebSocket command and return its result."""
        ws = await self.ws()
        self._id += 1
        await ws.send_json({"id": self._id, **payload})
        while True:
            message = await ws.receive_json(timeout=30)
            if message.get("id") == self._id and message.get("type") == "result":
                assert message["success"], message
                return message["result"]

    async def call(self, service: str, entry_id: str, **data: Any) -> dict[str, Any]:
        """Call a gmx_mail action like ha-mcp: return_response, config_entry_id in the data."""
        result = await self.rest("POST", f"/api/services/gmx_mail/{service}?return_response", {"config_entry_id": entry_id, **data})
        response: dict[str, Any] = result["service_response"]
        return response


async def _wait_for_ha(session: aiohttp.ClientSession) -> None:
    for _ in range(90):
        try:
            async with session.get(f"{HA_URL}/api/onboarding") as response:
                if response.status in (200, 401):
                    return
        except aiohttp.ClientError:
            pass
        await asyncio.sleep(2)
    pytest.fail(f"Home Assistant not reachable at {HA_URL}")


async def _onboard(session: aiohttp.ClientSession) -> str:
    """Create the owner user on a fresh instance and return an access token."""
    async with session.get(f"{HA_URL}/api/onboarding") as response:
        steps = await response.json() if response.status == 200 else []
    if not any(step["step"] == "user" and not step["done"] for step in steps):
        token = os.environ.get("STAGING_TOKEN")
        if not token:
            pytest.skip("Instance already onboarded: run 'docker compose --profile staging down -v' first or set STAGING_TOKEN")
        return token
    user = {"client_id": CLIENT_ID, "name": "Staging", "username": "staging", "password": secrets.token_urlsafe(24), "language": "de"}
    async with session.post(f"{HA_URL}/api/onboarding/users", json=user) as response:
        assert response.status == 200, await response.text()
        auth_code = (await response.json())["auth_code"]
    async with session.post(
        f"{HA_URL}/auth/token", data={"grant_type": "authorization_code", "code": auth_code, "client_id": CLIENT_ID}
    ) as response:
        assert response.status == 200, await response.text()
        return str((await response.json())["access_token"])


@pytest.fixture
async def staging() -> AsyncIterator[Staging]:
    """Onboarded Home Assistant API client."""
    if not CA_FILE.exists():
        pytest.skip("test CA missing - run tools/make_test_certs.py")
    url = urlsplit(HA_URL)
    try:
        socket.create_connection((url.hostname or "127.0.0.1", url.port or 8123), timeout=2).close()
    except OSError:
        if os.environ.get("REQUIRE_STAGING"):
            pytest.fail(f"Home Assistant not reachable at {HA_URL} (REQUIRE_STAGING is set)")
        pytest.skip(f"Home Assistant not reachable at {HA_URL}")
    async with aiohttp.ClientSession() as session:
        await _wait_for_ha(session)  # the container may still be starting
        client = Staging(session, await _onboard(session))
        yield client
        if client._ws is not None:
            await client._ws.close()


def _deliver(subject: str) -> None:
    context = ssl.create_default_context(cafile=str(CA_FILE))
    client = IMAPClient(MAIL_HOST, port=MAIL_PORT, ssl=True, ssl_context=context, timeout=15)
    client.login(USER, PASSWORD)
    message = EmailMessage()
    message["From"] = "Shop <shop@example.com>"
    message["To"] = USER
    message["Subject"] = subject
    message.set_content("Ihre Rechnung liegt bei.")
    client.append("INBOX", message.as_bytes())
    client.logout()


async def test_end_to_end(staging: Staging) -> None:
    """Config flow -> entities -> actions -> IDLE push event -> diagnostics, all through HA's APIs."""
    # 1. Config flow (TLS against Dovecot with the test CA, IMAP and SMTP login check)
    flow = await staging.rest("POST", "/api/config/config_entries/flow", {"handler": "gmx_mail"})
    assert flow["type"] == "form"
    result = await staging.rest(
        "POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {"email": USER, "password": PASSWORD, "server": SERVER_IN_COMPOSE}
    )
    assert result["type"] == "create_entry", result
    entry_id = result["result"]["entry_id"]

    # 2. Entities of the account and the push state
    entities = [e for e in await staging.command({"type": "config/entity_registry/list"}) if e["platform"] == "gmx_mail"]
    by_key = {e["unique_id"].removeprefix(f"{entry_id}_"): e["entity_id"] for e in entities}
    assert set(by_key) == {"unseen_inbox", "junk_messages", "last_sync", "actions_today", "connection"}
    for _ in range(30):
        connection = await staging.rest("GET", f"/api/states/{by_key['connection']}")
        if connection["attributes"].get("push") == "connected":
            break
        await asyncio.sleep(1)
    assert connection["state"] == "on"
    assert connection["attributes"]["push"] == "connected"

    # 3. Read actions as ha-mcp calls them
    status = await staging.call("get_status", entry_id)
    assert status["content_trust"] == "untrusted_external_email"
    assert status["data"]["folders_map"]["roles"]["junk"]["name"] == "Spamverdacht"
    assert status["data"]["push_state"] == "connected"
    folders = await staging.call("list_folders", entry_id, include_counts=True)
    assert {f["path"] for f in folders["data"]["folders"]} >= {"INBOX", "Gesendet", "Entwürfe", "Gelöscht", "Spamverdacht"}

    # 4. New mail -> IDLE -> coordinator -> event (no 5-minute wait)
    ws = await staging.ws()
    await ws.send_json({"id": 9000, "type": "subscribe_events", "event_type": "gmx_mail_new_message"})
    assert (await ws.receive_json(timeout=10))["success"]
    unseen_before = int((await staging.rest("GET", f"/api/states/{by_key['unseen_inbox']}"))["state"])
    await asyncio.to_thread(_deliver, "Staging-Rechnung")
    event = None
    while event is None:
        message = await ws.receive_json(timeout=30)
        if message.get("type") == "event":
            event = message["event"]["data"]
    assert event["entry_id"] == entry_id and event["folder"] == "INBOX"
    assert set(event) == {"entry_id", "folder", "ref"}  # no sender/subject without event_include_headers
    await asyncio.sleep(1)
    unseen_after = int((await staging.rest("GET", f"/api/states/{by_key['unseen_inbox']}"))["state"])
    assert unseen_after == unseen_before + 1

    # 5. Search and read the new message; it stays unread
    found = await staging.call("search", entry_id, subject="Staging-Rechnung")
    assert [m["ref"] for m in found["data"]["messages"]] == [event["ref"]]
    message = await staging.call("get_message", entry_id, ref=event["ref"])
    assert "Ihre Rechnung liegt bei." in message["data"]["body"]["text"]
    assert message["data"]["body"]["text"].startswith("<<<UNTRUSTED_EMAIL_CONTENT ")
    assert "\\Seen" not in message["data"]["flags"]

    # 6. Diagnostics without credentials or addresses
    diagnostics = await staging.rest("GET", f"/api/diagnostics/config_entry/{entry_id}")
    text = json.dumps(diagnostics)
    assert PASSWORD not in text and USER not in text
    assert diagnostics["data"]["push"]["state"] == "connected"

    # 7. Actions counter
    counter = await staging.rest("GET", f"/api/states/{by_key['actions_today']}")
    assert int(counter["state"]) == 4  # get_status, list_folders, search, get_message

    # 8. M4: enable the "organize" level through the options flow (as a person would in the UI)
    await _set_options(staging, entry_id, {**DEFAULT_OPTIONS, CONF_PERMISSIONS: ["read", "organize"]}, "organize")

    # 9. Organise: flag, create folder, move (new reference), draft; audit log without content
    flagged = await staging.call("set_flags", entry_id, refs=[event["ref"]], add=["flagged", "$Staging"])
    assert flagged["data"]["updated"] == 1
    created = await staging.call("create_folder", entry_id, name="Staging-Ablage")
    moved = await staging.call("move", entry_id, refs=[event["ref"]], target_folder=created["data"]["path"])
    new_ref = moved["data"]["new_refs"][0]
    assert new_ref["folder"] == "Staging-Ablage"
    reread = await staging.call("get_message", entry_id, ref=new_ref)
    assert "\\Flagged" in reread["data"]["flags"] and "$Staging" in reread["data"]["flags"]
    draft = await staging.call("save_draft", entry_id, to=["anna@example.org"], body_text="Entwurf", in_reply_to_ref=new_ref)
    assert draft["data"]["ref"]["folder"] == "Entwürfe"
    audit = await staging.call("get_audit_log", entry_id)
    assert [e["action"] for e in audit["data"]["entries"]] == ["save_draft", "move", "create_folder", "set_flags"]
    assert "anna@example.org" not in json.dumps(audit)

    # 10. M5: enable "delete" and permanent deletion; trash, two-step permanent deletion, folder deletion
    options = {**DEFAULT_OPTIONS, CONF_PERMISSIONS: ["read", "organize", "delete"], CONF_ALLOW_PERMANENT_DELETE: True}
    await _set_options(staging, entry_id, options, "delete")
    trashed = await staging.call("trash", entry_id, refs=[new_ref])
    in_trash = trashed["data"]["new_refs"][0]
    assert trashed["data"]["trash_folder"] == "Gelöscht" and in_trash["folder"] == "Gelöscht"
    preview = await staging.call("delete_permanently", entry_id, refs=[in_trash])
    assert preview["data"]["preview"]["count"] == 1 and preview["data"]["preview"]["messages"][0]["subject"] == "Staging-Rechnung"
    code = preview["data"]["confirm_token"]
    done = await staging.call("delete_permanently", entry_id, refs=[in_trash], confirm_token=code)
    assert done["data"]["deleted"] == 1
    try:
        await staging.call("delete_permanently", entry_id, refs=[in_trash], confirm_token=code)
        raise AssertionError("a confirmation code must work only once")
    except AssertionError as err:
        assert "400" in str(err), err
    folder_preview = await staging.call("delete_folder", entry_id, path="Staging-Ablage")
    gone = await staging.call("delete_folder", entry_id, path="Staging-Ablage", confirm_token=folder_preview["data"]["confirm_token"])
    assert gone["data"]["deleted_folder"] == "Staging-Ablage"
    status = await staging.call("get_status", entry_id)
    assert status["data"]["limits_used"]["deleted_today"] == 2  # trash + permanent deletion both count
    audit = await staging.call("get_audit_log", entry_id, limit=6)
    assert [(e["action"], e["outcome"]) for e in audit["data"]["entries"]] == [
        ("delete_folder", "ok"),
        ("delete_folder", "preview"),
        ("delete_permanently", "rejected"),
        ("delete_permanently", "ok"),
        ("delete_permanently", "preview"),
        ("trash", "ok"),
    ]

    # 11. M6: enable sending with an allowlist; preview + code, SMTP over STARTTLS (test CA), Sent copy; allowlist holds
    options |= {
        CONF_PERMISSIONS: ["read", "organize", "delete", "send"],
        CONF_SEND_MODE: "allowlist",
        CONF_ALLOWLIST: ["test@example.test"],
    }
    await _set_options(staging, entry_id, options, "send")
    mail = {"to": ["test@example.test"], "subject": "Staging-Versand", "body_text": "Gesendet aus dem Staging-Test."}
    preview = await staging.call("send_message", entry_id, **mail)
    assert preview["data"]["preview"]["to"] == ["test@example.test"] and preview["data"]["preview"]["sent_copy_mode"] == "append"
    sent = await staging.call("send_message", entry_id, **mail, confirm_token=preview["data"]["confirm_token"])
    assert sent["data"]["recipients"] == 1 and sent["data"]["sent_copy"] == "appended" and sent["warnings"] == []
    copies = await staging.call("search", entry_id, folder="Gesendet", subject="Staging-Versand")
    assert len(copies["data"]["messages"]) == 1
    try:
        await staging.call("send_message", entry_id, to=["fremd@example.net"], body_text="x")
        raise AssertionError("a recipient outside the allowlist must be refused")
    except AssertionError as err:
        assert "400" in str(err), err
    audit = await staging.call("get_audit_log", entry_id, limit=3)
    assert [(e["action"], e["outcome"]) for e in audit["data"]["entries"]] == [
        ("send_message", "rejected"),
        ("send_message", "ok"),
        ("send_message", "preview"),
    ]
    assert audit["data"]["entries"][1]["recipient_domains"] == ["example.test"]
    assert "test@example.test" not in json.dumps(audit)


async def _set_options(staging: Staging, entry_id: str, options: dict[str, Any], expected_level: str) -> None:
    """Change the options through the options flow (as a person would in the UI) and wait for the reload."""
    flow = await staging.rest("POST", "/api/config/config_entries/options/flow", {"handler": entry_id})
    result = await staging.rest("POST", f"/api/config/config_entries/options/flow/{flow['flow_id']}", nest(options))
    assert result["type"] == "create_entry", result
    status: dict[str, Any] = {}
    for _ in range(30):  # the entry reloads after the options change ("not loaded" for a moment)
        try:
            status = await staging.call("get_status", entry_id)
        except AssertionError:
            status = {}
        if expected_level in status.get("data", {}).get("permissions", []):
            return
        await asyncio.sleep(1)
    raise AssertionError(f"level {expected_level} not active after the options change")
