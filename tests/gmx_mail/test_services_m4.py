"""Tests for the organising actions (M4) as Home Assistant services, the audit log and attachment cleanup."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from imapclient.exceptions import IMAPClientAbortError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.gmx_mail.attachments import remove_old_files
from custom_components.gmx_mail.const import (
    CONF_ATTACHMENT_RETENTION_DAYS,
    CONF_PERMISSIONS,
    DEFAULT_OPTIONS,
    DOMAIN,
    EVENT_ACTION_EXECUTED,
)

from .fakes import FakeImapServer, plain_message


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, **options: Any) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={**DEFAULT_OPTIONS, CONF_PERMISSIONS: ["read", "organize"], **options})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def mailbox(imap_server: FakeImapServer) -> FakeImapServer:
    """INBOX with three messages; Archiv, Spamverdacht and Entwürfe exist."""
    inbox = imap_server.mailbox("INBOX", uidvalidity=777)
    for uid in (1, 2, 3):
        inbox.messages[uid] = plain_message(subject=f"m{uid}", message_id=f"<m{uid}@example.org>")
    for name in ("Archiv", "Spamverdacht", "Entwürfe", "Gesendet", "Gelöscht"):
        imap_server.mailbox(name, uidvalidity=900)
    return imap_server


def ref(uid: int, folder: str = "INBOX", validity: int = 777) -> dict[str, Any]:
    """Build a message reference."""
    return {"folder": folder, "uid": uid, "uidvalidity": validity}


async def call(hass: HomeAssistant, entry: MockConfigEntry, service: str, **data: Any) -> dict[str, Any]:
    """Call an action with return_response (as ha-mcp does)."""
    response = await hass.services.async_call(
        DOMAIN, service, {"config_entry_id": entry.entry_id, **data}, blocking=True, return_response=True
    )
    assert isinstance(response, dict)
    return response


async def test_organize_needs_permission(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """With the default options (read only) every organising action is refused before touching the server."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    for service, data in (
        ("set_flags", {"refs": [ref(1)], "add": ["seen"]}),
        ("move", {"refs": [ref(1)], "target_folder": "Archiv"}),
        ("copy", {"refs": [ref(1)], "target_folder": "Archiv"}),
        ("mark_spam", {"refs": [ref(1)]}),
        ("mark_not_spam", {"refs": [ref(1)]}),
        ("create_folder", {"name": "x"}),
        ("rename_folder", {"path": "Archiv", "new_name": "x"}),
        ("save_draft", {"body_text": "x"}),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, config_entry, service, **data)
        assert err.value.translation_key == "permission_disabled"
    assert not [c for c in mailbox.commands if c[0] in ("add_flags", "move", "copy", "create", "rename", "append")]


async def test_writing_actions_may_be_called_without_response(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer
) -> None:
    """Writing actions support an optional response (automations can call them without return_response)."""
    await _setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN, "set_flags", {"config_entry_id": config_entry.entry_id, "refs": [ref(1)], "add": ["flagged"]}, blocking=True
    )
    assert mailbox.mailbox("INBOX").messages[1].flags == (b"\\Flagged",)


async def test_set_flags_and_audit(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Flags are set; the audit log and event contain no content; the counter counts the 'organize' level."""
    events = async_capture_events(hass, EVENT_ACTION_EXECUTED)
    await _setup(hass, config_entry)
    response = await call(hass, config_entry, "set_flags", refs=[ref(1), ref(2), ref(1)], add=["seen", "$Steuer"])
    assert response["data"] == {"updated": 2, "missing": []}
    assert mailbox.mailbox("INBOX").messages[2].flags == (b"\\Seen", b"$Steuer")
    assert len(events) == 1
    event = events[0].data
    assert event["entry_id"] == config_entry.entry_id
    assert event["action"] == "set_flags" and event["level"] == "organize" and event["outcome"] == "ok" and event["count"] == 2
    assert event["folders"] == ["INBOX"] and len(event["hash"]) == 16
    assert "$Steuer" not in str(event) and "seen" not in str(event)
    log = await call(hass, config_entry, "get_audit_log")
    assert [e["action"] for e in log["data"]["entries"]] == ["set_flags"]
    assert config_entry.runtime_data.counter.counts == {"organize": 1, "read": 1}


async def test_set_flags_validation(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """No flags, \\Deleted and too many references are refused."""
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, config_entry, "set_flags", refs=[ref(1)])
    assert err.value.translation_key == "no_flags"
    for flag in ("\\Deleted", "deleted", "\\Draft"):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, config_entry, "set_flags", refs=[ref(1)], add=[flag])
        assert err.value.translation_key == "invalid_flag"
    with pytest.raises(Exception, match=r"."):
        await call(hass, config_entry, "set_flags", refs=[ref(i) for i in range(1, 102)], add=["seen"])


async def test_move_copy_and_spam(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Move/copy return new references; spam goes to Spamverdacht and back to the inbox."""
    await _setup(hass, config_entry)
    moved = await call(hass, config_entry, "move", refs=[ref(1)], target_folder="Archiv")
    assert moved["data"] == {"moved": 1, "target_folder": "Archiv", "new_refs": [ref(1, "Archiv", 900)], "missing": []}
    copied = await call(hass, config_entry, "copy", refs=[ref(2)], target_folder="Archiv")
    assert copied["data"]["copied"] == 1 and copied["data"]["new_refs"] == [ref(2, "Archiv", 900)]
    spam = await call(hass, config_entry, "mark_spam", refs=[ref(3)])
    assert spam["data"]["target_folder"] == "Spamverdacht"
    back = await call(hass, config_entry, "mark_not_spam", refs=spam["data"]["new_refs"])
    assert back["data"]["target_folder"] == "INBOX"
    assert sorted(mailbox.mailbox("INBOX").messages) == [2, 4]
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, config_entry, "move", refs=[ref(2)], target_folder="Nope")
    assert err.value.translation_key == "folder_not_found"
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, config_entry, "move", refs=[ref(2, validity=1)], target_folder="Archiv")
    assert err.value.translation_key == "uidvalidity_changed"
    actions = [e["action"] for e in (await call(hass, config_entry, "get_audit_log"))["data"]["entries"]]
    assert actions == ["mark_not_spam", "mark_spam", "copy", "move"]  # newest first; refused calls are not logged


async def test_connection_loss_during_write_is_not_repeated(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer
) -> None:
    """A dropped connection during MOVE yields 'outcome unknown', is audited and is not retried."""
    await _setup(hass, config_entry)
    mailbox.write_error = IMAPClientAbortError("socket error: EOF")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, config_entry, "move", refs=[ref(1)], target_folder="Archiv")
    assert err.value.translation_key == "outcome_unknown"
    assert not isinstance(err.value, ServiceValidationError)
    assert [c for c in mailbox.commands if c[0] == "move"] == []  # the failing attempt was the only one
    entries = (await call(hass, config_entry, "get_audit_log"))["data"]["entries"]
    assert entries[0]["action"] == "move" and entries[0]["outcome"] == "unknown"


async def test_folders(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Create and rename folders; special folders are protected."""
    await _setup(hass, config_entry)
    created = await call(hass, config_entry, "create_folder", name="Rechnungen")
    assert created["data"] == {"path": "Rechnungen"}
    sub = await call(hass, config_entry, "create_folder", name="2026", parent="Rechnungen")
    assert sub["data"] == {"path": "Rechnungen/2026"}
    renamed = await call(hass, config_entry, "rename_folder", path="Rechnungen/2026", new_name="2026 erledigt")
    assert renamed["data"] == {"path": "Rechnungen/2026 erledigt", "previous_path": "Rechnungen/2026"}
    for service, data, key in (
        ("create_folder", {"name": "x", "parent": "Gesendet"}, "protected_folder"),
        ("rename_folder", {"path": "INBOX", "new_name": "x"}, "protected_folder"),
        ("create_folder", {"name": "a/b"}, "invalid_folder_name"),
        ("create_folder", {"name": "Rechnungen"}, "folder_exists"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, config_entry, service, **data)
        assert err.value.translation_key == key


async def test_save_draft(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer) -> None:
    """Drafts land in Entwürfe; nothing is sent; addresses are validated; the audit log has no recipients."""
    events = async_capture_events(hass, EVENT_ACTION_EXECUTED)
    await _setup(hass, config_entry)
    response = await call(
        hass, config_entry, "save_draft", to=["anna@example.org"], subject="Hallo", body_text="Text", in_reply_to_ref=ref(2)
    )
    assert response["data"]["ref"] == ref(1, "Entwürfe", 900)
    assert "anna@example.org" not in str(events[0].data) and "Hallo" not in str(events[0].data)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, config_entry, "save_draft", to=["kaputt"], body_text="x")
    assert err.value.translation_key == "invalid_address"


async def test_audit_log_survives_restart_and_expires(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, freezer: FrozenDateTimeFactory
) -> None:
    """Entries are stored and dropped after the retention period."""
    await _setup(hass, config_entry)
    await call(hass, config_entry, "set_flags", refs=[ref(1)], add=["seen"])
    freezer.tick(10)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len((await call(hass, config_entry, "get_audit_log"))["data"]["entries"]) == 1
    freezer.tick(91 * 86400)
    assert (await call(hass, config_entry, "get_audit_log"))["data"]["entries"] == []


def test_remove_old_files(tmp_path: Path) -> None:
    """Only old regular files directly in the directory are removed; 0 days keeps everything."""
    now = time.time()
    old, new = tmp_path / "old.pdf", tmp_path / "new.pdf"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    os.utime(old, (now - 8 * 86400, now - 8 * 86400))
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.pdf").write_bytes(b"z")
    os.utime(sub / "deep.pdf", (now - 30 * 86400, now - 30 * 86400))
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"o")
    (tmp_path / "link.pdf").symlink_to(outside)
    assert remove_old_files(tmp_path, 0, now) == 0
    assert remove_old_files(tmp_path, 7, now) == 1
    assert not old.exists() and new.exists() and (sub / "deep.pdf").exists() and outside.exists()
    assert remove_old_files(tmp_path / "missing", 7, now) == 0


async def test_attachment_cleanup_runs_at_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, tmp_path: Path
) -> None:
    """Saved attachments older than the option are removed in the account's own folder only."""
    hass.config.media_dirs = {"local": str(tmp_path)}
    own = tmp_path / "gmx_mail" / config_entry.entry_id
    own.mkdir(parents=True)
    other = tmp_path / "other.pdf"
    other.write_bytes(b"x")
    old = own / "1_2_old.pdf"
    old.write_bytes(b"x")
    past = time.time() - 3 * 86400
    os.utime(old, (past, past))
    os.utime(other, (past, past))
    await _setup(hass, config_entry, **{CONF_ATTACHMENT_RETENTION_DAYS: 2})
    for _ in range(5):
        await hass.async_block_till_done()
    assert not old.exists()
    assert other.exists()
