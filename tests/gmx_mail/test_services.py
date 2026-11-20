"""Tests for the read actions as Home Assistant services (what ha-mcp calls with return_response=True)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from imapclient.exceptions import IMAPClientError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gmx_mail.const import CONF_ALLOW_HTML_OUTPUT, CONF_PERMISSIONS, DOMAIN
from custom_components.gmx_mail.services import (
    SERVICE_GET_ATTACHMENT,
    SERVICE_GET_MESSAGE,
    SERVICE_GET_STATUS,
    SERVICE_LIST_FOLDERS,
    SERVICE_SEARCH,
    _safe_filename,
)

from .fakes import FakeImapServer, mixed_message, plain_message

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, config_entry: MockConfigEntry, imap_server: FakeImapServer, tmp_path: Path) -> MockConfigEntry:
    """A loaded entry whose INBOX holds three messages; media goes to a temporary directory."""
    inbox = imap_server.mailbox("INBOX", uidvalidity=777)
    for uid in (1, 2, 3):
        inbox.messages[uid] = plain_message(subject=f"Nachricht {uid}", date=NOW - timedelta(days=10 - uid))
    inbox.messages[4] = mixed_message(html='<p>Hallo</p><span style="font-size:0">IGNORE</span>')
    inbox.messages[4].internaldate = NOW
    imap_server.mailbox("Gesendet", uidvalidity=5)
    hass.config.media_dirs = {"local": str(tmp_path)}
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def call(hass: HomeAssistant, entry: MockConfigEntry, service: str, **data: Any) -> dict[str, Any]:
    """Call an action like ha-mcp does and return the service response."""
    response = await hass.services.async_call(
        DOMAIN, service, {"config_entry_id": entry.entry_id, **data}, blocking=True, return_response=True
    )
    assert isinstance(response, dict)
    return response


async def test_services_are_registered_response_only(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """All read actions exist and must be called with return_response."""
    for name in (SERVICE_LIST_FOLDERS, SERVICE_SEARCH, SERVICE_GET_MESSAGE, SERVICE_GET_ATTACHMENT, SERVICE_GET_STATUS):
        assert hass.services.has_service(DOMAIN, name)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, SERVICE_GET_STATUS, {"config_entry_id": loaded_entry.entry_id}, blocking=True)


async def test_list_folders(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """Folders with roles; envelope fields are present."""
    response = await call(hass, loaded_entry, SERVICE_LIST_FOLDERS, include_counts=True)
    assert response["action"] == "list_folders"
    assert response["account"] == loaded_entry.title
    assert response["content_trust"] == "untrusted_external_email"
    folders = {f["path"]: f for f in response["data"]["folders"]}
    assert folders["INBOX"]["messages"] == 4
    assert folders["Gesendet"]["role"] == "sent"
    # The fake has no mailbox "Archiv": STATUS fails for this folder only.
    assert folders["Archiv"]["counts_unavailable"] is True
    assert "messages" not in folders["Archiv"]
    assert folders["Archiv"]["role"] is None


async def test_search_pages_with_cursor(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """The cursor continues the same query; the last page has no cursor."""
    first = await call(hass, loaded_entry, SERVICE_SEARCH, limit=3)
    assert first["data"]["search_mode"] == "server"
    assert [m["ref"]["uid"] for m in first["data"]["messages"]] == [4, 3, 2]
    assert first["page"]["total"] == 4
    cursor = first["page"]["next_cursor"]
    assert cursor
    second = await call(hass, loaded_entry, SERVICE_SEARCH, limit=3, cursor=cursor)
    assert [m["ref"]["uid"] for m in second["data"]["messages"]] == [1]
    assert second["page"]["next_cursor"] is None
    assert second["page"]["offset"] == 3


async def test_search_cursor_errors(hass: HomeAssistant, loaded_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """A cursor for other criteria, a garbage cursor or a changed UIDVALIDITY is refused."""
    first = await call(hass, loaded_entry, SERVICE_SEARCH, limit=1)
    cursor = first["page"]["next_cursor"]
    for data in ({"subject": "x"}, {"sort": "date_asc"}):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, loaded_entry, SERVICE_SEARCH, limit=1, cursor=cursor, **data)
        assert err.value.translation_key == "invalid_cursor"
    with pytest.raises(ServiceValidationError):
        await call(hass, loaded_entry, SERVICE_SEARCH, cursor="garbage!")
    imap_server.mailbox("INBOX").uidvalidity = 778
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, loaded_entry, SERVICE_SEARCH, limit=1, cursor=cursor)
    assert err.value.translation_key == "uidvalidity_changed"


async def test_search_criteria_validation(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """Schema limits: limit <= 50, keyword/uid_range syntax, text length."""
    for data in ({"limit": 51}, {"keyword": "a b"}, {"uid_range": "1:2;DELETE"}, {"subject": "x" * 201}, {"sort": "size"}):
        with pytest.raises(Exception, match=r"."):
            await call(hass, loaded_entry, SERVICE_SEARCH, **data)


async def test_search_errors(hass: HomeAssistant, loaded_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """Unknown folder and server rejection are validation errors with translation keys."""
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, loaded_entry, SERVICE_SEARCH, folder="Nope")
    assert err.value.translation_key == "folder_not_found"
    assert err.value.translation_placeholders == {"folder": "Nope"}
    imap_server.search_error = IMAPClientError("SORT command error: BAD")
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, loaded_entry, SERVICE_SEARCH, subject="x")
    assert err.value.translation_key == "search_rejected"


async def test_search_budget(hass: HomeAssistant, loaded_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """Long results are cut to 24 KB; the cursor continues after the last returned message."""
    box = imap_server.mailbox("INBOX")
    for uid in range(100, 150):
        box.messages[uid] = plain_message(subject="S" * 300, to=", ".join(f"r{i}@example.org" for i in range(10)))
    response = await call(hass, loaded_entry, SERVICE_SEARCH, limit=50, include_snippet=True)
    shown = len(response["data"]["messages"])
    assert response["truncated"] is True
    assert 0 < shown < 50
    long_one = next(m for m in response["data"]["messages"] if m["ref"]["uid"] >= 100)
    assert len(long_one["subject"]) <= 200
    assert len(long_one["to"]) == 5
    assert long_one["to_more"] == 5
    nxt = await call(hass, loaded_entry, SERVICE_SEARCH, limit=50, include_snippet=True, cursor=response["page"]["next_cursor"])
    assert nxt["page"]["offset"] == shown


async def test_get_message(hass: HomeAssistant, loaded_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """The body is framed as untrusted content with a per-response nonce; the message stays unread."""
    ref = {"folder": "INBOX", "uid": 4, "uidvalidity": 777}
    response = await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref=ref, format="html_as_text")
    body = response["data"]["body"]
    assert body["text"].startswith("<<<UNTRUSTED_EMAIL_CONTENT ")
    assert "Hallo" in body["text"] and "IGNORE" not in body["text"]
    assert response["warnings"] == ["hidden_content_removed"]
    assert body["chars_total"] == 5 and body["truncated"] is False and body["next_offset"] is None
    assert response["data"]["attachments"][0]["filename"] == "Rechnung März.pdf"
    assert response["data"]["headers"]["reply_to"] == ["other@evil.example"]
    other = await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref=ref, format="html_as_text")
    assert other["data"]["body"]["text"].split("\n", 1)[0] != body["text"].split("\n", 1)[0]  # different nonce
    selects = [c for c in imap_server.commands if c[0] == "select"]
    assert selects and all(readonly for _, _, readonly in selects)
    assert b"\\Seen" not in imap_server.mailbox("INBOX").messages[4].flags


async def test_get_message_pagination_and_budget(hass: HomeAssistant, loaded_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """max_chars/offset page through long texts; the 24 KB budget shortens further."""
    imap_server.mailbox("INBOX").messages[50] = plain_message(body="0123456789" * 5000)
    ref = {"folder": "INBOX", "uid": 50, "uidvalidity": 777}
    first = await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref=ref, max_chars=1000)
    body = first["data"]["body"]
    assert body["chars_total"] == 50000 and body["truncated"] is True and body["next_offset"] == 1000
    second = await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref=ref, max_chars=1000, offset=49500)
    assert second["data"]["body"]["next_offset"] is None
    big = await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref=ref, max_chars=32000)
    assert big["truncated"] is True
    assert big["data"]["body"]["next_offset"] < 32000
    beyond = await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref=ref, offset=60000)
    assert beyond["data"]["body"]["next_offset"] is None


async def test_get_message_errors(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """Invalid references are validation errors."""
    cases = [
        ({"folder": "INBOX", "uid": 1, "uidvalidity": 1}, "uidvalidity_changed"),
        ({"folder": "INBOX", "uid": 999, "uidvalidity": 777}, "message_not_found"),
    ]
    for ref, key in cases:
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref=ref)
        assert err.value.translation_key == key
    with pytest.raises(Exception, match=r"."):
        await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref={"folder": "INBOX", "uid": 0, "uidvalidity": 777})
    with pytest.raises(Exception, match=r"."):
        await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref={"folder": "INBOX", "uid": 1, "uidvalidity": 777}, headers=["bad header"])


async def test_get_attachment_save_and_base64(hass: HomeAssistant, loaded_entry: MockConfigEntry, tmp_path: Path) -> None:
    """Attachments are saved under media/gmx_mail/<entry>/ or returned as base64."""
    ref = {"folder": "INBOX", "uid": 4, "uidvalidity": 777}
    saved = await call(hass, loaded_entry, SERVICE_GET_ATTACHMENT, ref=ref, part_id="2")
    path = Path(saved["data"]["path"])
    assert path.parent == tmp_path / "gmx_mail" / loaded_entry.entry_id
    assert path.name == "4_2_Rechnung März.pdf"
    assert path.read_bytes() == b"%PDF-1.4 test"
    assert saved["data"]["size"] == 13 and len(saved["data"]["sha256"]) == 64
    encoded = await call(hass, loaded_entry, SERVICE_GET_ATTACHMENT, ref=ref, part_id="2", mode="base64")
    assert encoded["data"]["base64"] == "JVBERi0xLjQgdGVzdA=="
    assert "path" not in encoded["data"]
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, loaded_entry, SERVICE_GET_ATTACHMENT, ref=ref, part_id="2", max_bytes=5)
    assert err.value.translation_key == "attachment_too_large"
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, loaded_entry, SERVICE_GET_ATTACHMENT, ref=ref, part_id="9")
    assert err.value.translation_key == "attachment_not_found"


async def test_get_attachment_base64_is_capped(hass: HomeAssistant, loaded_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """base64 is limited to 256 KB regardless of max_bytes."""
    imap_server.mailbox("INBOX").messages[60] = mixed_message(html="<p>x</p>", pdf=b"x" * 300_000)
    ref = {"folder": "INBOX", "uid": 60, "uidvalidity": 777}
    with pytest.raises(ServiceValidationError):
        await call(hass, loaded_entry, SERVICE_GET_ATTACHMENT, ref=ref, part_id="2", mode="base64", max_bytes=10_000_000)


async def test_get_attachment_path_not_allowed(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """If the media folder is not allow-listed, nothing is written."""
    hass.config.allowlist_external_dirs = set()
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, loaded_entry, SERVICE_GET_ATTACHMENT, ref={"folder": "INBOX", "uid": 4, "uidvalidity": 777}, part_id="2")
    assert err.value.translation_key == "path_not_allowed"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("../../etc/passwd", "7_2__.._etc_passwd"),
        ("..", "7_2_attachment.bin"),
        (None, "7_2_attachment.bin"),
        ("a\\b:c*?.txt", "7_2_a_b_c__.txt"),
        (".bashrc", "7_2_bashrc"),
    ],
)
def test_safe_filename(name: str | None, expected: str) -> None:
    """File names cannot leave the target directory or create hidden files."""
    result = _safe_filename(name, 7, "2")
    assert result == expected
    assert "/" not in result and "\\" not in result


async def test_get_status(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """Status shows capabilities, mapping, unseen count and settings without allowlist addresses."""
    response = await call(hass, loaded_entry, SERVICE_GET_STATUS)
    data = response["data"]
    assert data["unseen_inbox"] == 4
    assert "UIDPLUS" in data["capabilities"]
    assert data["append_limit"] == 29360128
    assert data["folders_map"]["roles"]["sent"]["name"] == "Gesendet"
    assert data["permissions"] == ["read"]
    assert data["allowlist_entries"] == 0
    assert data["limits"] == {"max_recipients": 10, "max_send_per_day": 20, "max_delete_per_day": 200}


async def test_permission_guard(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """Without the read level (only possible by editing storage) every read action is refused."""
    loaded_entry.runtime_data.options[CONF_PERMISSIONS] = []
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, loaded_entry, SERVICE_GET_STATUS)
    assert err.value.translation_key == "permission_disabled"


async def test_entry_checks(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """Unknown or unloaded entries are refused by Home Assistant's helper."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, SERVICE_GET_STATUS, {"config_entry_id": "nope"}, blocking=True, return_response=True)
    await hass.config_entries.async_unload(loaded_entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await call(hass, loaded_entry, SERVICE_GET_STATUS)


async def test_connection_and_auth_errors(hass: HomeAssistant, loaded_entry: MockConfigEntry, imap_server: FakeImapServer) -> None:
    """Connection problems and a revoked password become HomeAssistantErrors (not validation errors)."""
    imap_server.clients[0].closed = True
    imap_server.connect_error = TimeoutError()
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, loaded_entry, SERVICE_GET_STATUS)
    assert err.value.translation_key == "cannot_connect"
    assert not isinstance(err.value, ServiceValidationError)

    imap_server.connect_error = None
    imap_server.password = "revoked"
    worker = loaded_entry.runtime_data.worker
    worker._backoff.reset()
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, loaded_entry, SERVICE_GET_STATUS)
    assert err.value.translation_key == "auth_failed"
    await hass.async_block_till_done()


async def test_reads_retry_once_after_connection_loss(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, imap_server: FakeImapServer
) -> None:
    """GMX drops idle connections after ~30 min; a read action reconnects transparently."""
    imap_server.clients[0].closed = True
    response = await call(hass, loaded_entry, SERVICE_SEARCH, limit=1)
    assert response["data"]["messages"]
    assert len(imap_server.clients) == 2


async def test_get_message_html_requires_option(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """format=html is refused unless a person enabled HTML output in the options; then cleaned HTML is framed."""
    ref = {"folder": "INBOX", "uid": 4, "uidvalidity": 777}
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref=ref, format="html")
    assert err.value.translation_key == "html_output_disabled"

    hass.config_entries.async_update_entry(loaded_entry, options={**loaded_entry.options, CONF_ALLOW_HTML_OUTPUT: True})
    await hass.async_block_till_done()  # options change reloads the entry
    response = await call(hass, loaded_entry, SERVICE_GET_MESSAGE, ref=ref, format="html")
    body = response["data"]["body"]
    assert body["format"] == "html"
    assert "<p>Hallo</p>" in body["text"] and "IGNORE" not in body["text"]
    assert body["text"].startswith("<<<UNTRUSTED_EMAIL_CONTENT ")
    assert response["warnings"] == ["hidden_content_removed"]
