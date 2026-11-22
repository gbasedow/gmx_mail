"""Tests for the sending actions (M6) as Home Assistant services: rules, confirmation codes, limits, Sent copy, audit."""

from __future__ import annotations

import smtplib
from email import message_from_bytes, policy
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gmx_mail.attachments import outbox_dir
from custom_components.gmx_mail.const import (
    CONF_ALLOW_REPLY_TO_SENDER,
    CONF_ALLOWLIST,
    CONF_MAX_MESSAGE_SIZE_MB,
    CONF_MAX_RECIPIENTS,
    CONF_MAX_RECIPIENTS_PER_HOUR,
    CONF_MAX_SEND_PER_DAY,
    CONF_PERMISSIONS,
    CONF_SEND_MODE,
    CONF_SENDER_NAME,
    CONF_SENT_COPY_MODE,
    DEFAULT_OPTIONS,
    DOMAIN,
)

from .fakes import EMAIL, FakeImapServer, FakeSmtpServer, plain_message

ALLOWED = "anna@example.org"


@pytest.fixture
def mailbox(imap_server: FakeImapServer) -> FakeImapServer:
    """INBOX: 1 from an allowed sender, 2 from a shop with a different Reply-To, 3 with Cc; drafts and Sent exist."""
    inbox = imap_server.mailbox("INBOX", uidvalidity=777)
    inbox.messages[1] = plain_message(subject="Frage", sender=f"Anna <{ALLOWED}>", message_id="<m1@example.org>")
    inbox.messages[2] = plain_message(
        subject="Rechnung", sender="Shop <shop@example.com>", message_id="<m2@example.com>", extra_headers="Reply-To: pay@evil.test\r\n"
    )
    inbox.messages[3] = plain_message(
        subject="Runde", sender=f"Anna <{ALLOWED}>", message_id="<m3@example.org>", extra_headers="Cc: chef@firma.de, x@fremd.test\r\n"
    )
    for name in ("Gesendet", "Entwürfe", "Gelöscht", "Spamverdacht", "Archiv"):
        imap_server.mailbox(name, uidvalidity=900)
    return imap_server


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, **options: Any) -> None:
    entry.add_to_hass(hass)
    base = {
        **DEFAULT_OPTIONS,
        CONF_PERMISSIONS: ["read", "send"],
        CONF_SEND_MODE: "allowlist",
        CONF_ALLOWLIST: [ALLOWED, "*@firma.de"],
    }
    hass.config_entries.async_update_entry(entry, options={**base, **options})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


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


async def refused(hass: HomeAssistant, entry: MockConfigEntry, service: str, **data: Any) -> ServiceValidationError:
    """Call an action that must be refused."""
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, entry, service, **data)
    return err.value


async def send(hass: HomeAssistant, entry: MockConfigEntry, service: str, **data: Any) -> dict[str, Any]:
    """Preview, then send with the code from the preview."""
    preview = await call(hass, entry, service, **data)
    return await call(hass, entry, service, **data, confirm_token=preview["data"]["confirm_token"])


async def test_sending_needs_level_and_send_mode(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """Without the Send level or with send mode off nothing is composed or sent."""
    await _setup(hass, config_entry, **{CONF_PERMISSIONS: ["read", "organize", "delete"]})
    for service, data in (
        ("send_message", {"to": [ALLOWED], "body_text": "x"}),
        ("reply", {"ref": ref(1), "body_text": "x"}),
        ("forward", {"ref": ref(1), "to": [ALLOWED]}),
        ("send_draft", {"ref": ref(1, "Entwürfe", 900)}),
    ):
        assert (await refused(hass, config_entry, service, **data)).translation_key == "permission_disabled"
    await hass.config_entries.async_unload(config_entry.entry_id)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_PERMISSIONS: ["read", "send"], CONF_SEND_MODE: "off"}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert (await refused(hass, config_entry, "send_message", to=[ALLOWED], body_text="x")).translation_key == "sending_disabled"
    assert smtp_server.delivered == []


async def test_send_message_two_steps_with_sent_copy(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """Preview + code; the code is bound to the exact content; the Sent copy is appended; audit has domains only."""
    await _setup(hass, config_entry, **{CONF_SENDER_NAME: "Haus"})
    data = {"to": [ALLOWED], "bcc": ["chef@firma.de"], "subject": "Termin", "body_text": "Morgen um 10?"}
    preview = await call(hass, config_entry, "send_message", **data)
    p = preview["data"]["preview"]
    assert p["to"] == [ALLOWED] and p["bcc"] == ["chef@firma.de"] and p["text_start"].startswith("Morgen um 10?")
    assert p["from"] == f"Haus <{EMAIL}>" and p["sent_copy_mode"] == "append" and smtp_server.delivered == []
    code = preview["data"]["confirm_token"]
    changed = await refused(hass, config_entry, "send_message", **{**data, "body_text": "Morgen um 11?"}, confirm_token=code)
    assert changed.translation_key == "confirm_token_invalid"  # the code covers exactly the previewed text
    preview = await call(hass, config_entry, "send_message", **data)
    done = await call(hass, config_entry, "send_message", **data, confirm_token=preview["data"]["confirm_token"])
    assert done["data"]["recipients"] == 2 and done["data"]["sent_copy"] == "appended" and done["warnings"] == []

    (sender, recipients, raw) = smtp_server.delivered[0]
    assert sender == EMAIL and recipients == [ALLOWED, "chef@firma.de"]
    assert b"Bcc" not in raw  # hidden from the other recipients
    sent_box = mailbox.mailboxes["Gesendet"]
    (stored,) = sent_box.messages.values()
    assert b"\\Seen" in stored.flags and b"Bcc: chef@firma.de" in stored.header
    log = (await call(hass, config_entry, "get_audit_log"))["data"]["entries"]
    assert log[0]["action"] == "send_message" and log[0]["outcome"] == "ok"
    assert log[0]["recipient_domains"] == ["example.org", "firma.de"] and ALLOWED not in str(log)
    status = await call(hass, config_entry, "get_status")
    # "actions today" counts each successful call once: two previews + one sending; the refused call does not count.
    assert status["data"]["limits_used"]["actions_today"]["send"] == 3


async def test_allowlist_blocks_and_reports_domains_only(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """A recipient outside the allowlist blocks the whole message; the error names count and domains, not addresses."""
    await _setup(hass, config_entry)
    err = await refused(hass, config_entry, "send_message", to=[ALLOWED, "x@sub.firma.de"], body_text="x")
    assert err.translation_key == "recipient_not_allowed"
    assert err.translation_placeholders == {"count": "1", "domains": "sub.firma.de"}
    assert (await refused(hass, config_entry, "send_message", to=["kaputt"], body_text="x")).translation_key == "invalid_address"
    log = (await call(hass, config_entry, "get_audit_log"))["data"]["entries"]
    assert log[1]["outcome"] == "rejected" and log[1]["error"] == "recipient_not_allowed"
    assert smtp_server.delivered == []


async def test_reply_rules_reply_to_and_reply_all(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """The reply exception covers the From address only; a different Reply-To and extra reply-all recipients need the allowlist."""
    await _setup(hass, config_entry, **{CONF_ALLOW_REPLY_TO_SENDER: True})
    # Shop mail with Reply-To pay@evil.test: the reply would go there - not covered by the exception.
    err = await refused(hass, config_entry, "reply", ref=ref(2), body_text="Bezahlt")
    assert err.translation_key == "recipient_not_allowed" and err.translation_placeholders == {"count": "1", "domains": "evil.test"}
    # Reply-all to mail 3: chef@firma.de is allowed (*@firma.de), x@fremd.test is not.
    assert (await refused(hass, config_entry, "reply", ref=ref(3), body_text="ok", reply_all=True)).translation_key == (
        "recipient_not_allowed"
    )
    done = await send(hass, config_entry, "reply", ref=ref(1), body_text="Gern.")
    assert done["data"]["recipients"] == 1 and done["warnings"] == []
    raw = smtp_server.delivered[0][2]
    message = message_from_bytes(raw, policy=policy.default)
    assert str(message["Subject"]) == "Re: Frage" and str(message["In-Reply-To"]) == "<m1@example.org>"
    assert b"\\Answered" in mailbox.mailboxes["INBOX"].messages[1].flags


async def test_reply_to_unlisted_sender_needs_the_option(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """Without the option a reply to a sender outside the allowlist is refused; with it, it is allowed."""
    inbox = mailbox.mailboxes["INBOX"]
    inbox.messages[4] = plain_message(subject="Hallo", sender="Bob <bob@example.net>", message_id="<m4@example.net>")
    await _setup(hass, config_entry, **{CONF_ALLOW_REPLY_TO_SENDER: False})
    assert (await refused(hass, config_entry, "reply", ref=ref(4), body_text="x")).translation_key == "recipient_not_allowed"
    await hass.config_entries.async_unload(config_entry.entry_id)
    hass.config_entries.async_update_entry(config_entry, options={**config_entry.options, CONF_ALLOW_REPLY_TO_SENDER: True})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert (await send(hass, config_entry, "reply", ref=ref(4), body_text="x"))["data"]["recipients"] == 1


async def test_forward_and_send_draft(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """Forward with Fwd: subject; a sent draft is removed from the drafts folder (no Delete level needed)."""
    await _setup(hass, config_entry)
    done = await send(hass, config_entry, "forward", ref=ref(2), to=[ALLOWED], comment="Zur Info")
    assert done["data"]["recipients"] == 1
    assert str(message_from_bytes(smtp_server.delivered[0][2], policy=policy.default)["Subject"]) == "Fwd: Rechnung"

    draft = (f"To: {ALLOWED}\r\nSubject: Entwurf\r\nMessage-ID: <d9@gmx.test>\r\nContent-Type: text/plain\r\n\r\nHallo\r\n").encode()
    client = mailbox("imap.gmx.net", 993)
    client.login(EMAIL, mailbox.password)
    client.append("Entwürfe", draft, flags=("\\Draft",))
    (uid,) = mailbox.mailboxes["Entwürfe"].messages
    assert (await refused(hass, config_entry, "send_draft", ref=ref(1))).translation_key == "not_a_draft"
    sent = await send(hass, config_entry, "send_draft", ref=ref(uid, "Entwürfe", 900))
    assert sent["data"]["recipients"] == 1 and sent["warnings"] == []
    assert not mailbox.mailboxes["Entwürfe"].messages
    assert str(message_from_bytes(smtp_server.delivered[1][2], policy=policy.default)["From"]) == EMAIL


async def test_limits(hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer) -> None:
    """Recipients per message, recipients per hour, messages per day and size are checked before sending."""
    await _setup(
        hass,
        config_entry,
        **{CONF_MAX_RECIPIENTS: 2, CONF_MAX_RECIPIENTS_PER_HOUR: 3, CONF_MAX_SEND_PER_DAY: 2, CONF_MAX_MESSAGE_SIZE_MB: 1},
    )
    three = [ALLOWED, "a@firma.de", "b@firma.de"]
    err = await refused(hass, config_entry, "send_message", to=three, body_text="x")
    assert err.translation_key == "too_many_recipients" and err.translation_placeholders == {"limit": "2"}
    assert (await refused(hass, config_entry, "send_message", to=[ALLOWED], body_text="ä" * 600_000)).translation_key == "message_too_large"
    await send(hass, config_entry, "send_message", to=[ALLOWED, "a@firma.de"], body_text="1")
    err = await refused(hass, config_entry, "send_message", to=[ALLOWED, "b@firma.de"], body_text="2")
    assert err.translation_key == "hourly_recipient_limit" and err.translation_placeholders == {"limit": "3", "remaining": "1"}
    await send(hass, config_entry, "send_message", to=[ALLOWED], body_text="3")
    assert (await refused(hass, config_entry, "send_message", to=["c@firma.de"], body_text="4")).translation_key == "daily_send_limit"
    assert len(smtp_server.delivered) == 2


async def test_outbox_attachments(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer, tmp_path: Path
) -> None:
    """Attachments only from the outbox folder: plain names, no paths, no symlinks; the code covers the file content."""
    hass.config.media_dirs = {"local": str(tmp_path)}
    outbox = outbox_dir(hass)
    outbox.mkdir(parents=True)
    (outbox / "plan.pdf").write_bytes(b"%PDF-1.4 plan")
    (tmp_path / "geheim.txt").write_text("secret")
    (outbox / "link.txt").symlink_to(tmp_path / "geheim.txt")
    await _setup(hass, config_entry)
    for name, key in (
        ("../geheim.txt", "attachment_not_allowed"),
        (".hidden", "attachment_not_allowed"),
        ("link.txt", "attachment_missing"),
        ("fehlt.pdf", "attachment_missing"),
    ):
        assert (await refused(hass, config_entry, "send_message", to=[ALLOWED], body_text="x", attachments=[name])).translation_key == key
    preview = await call(hass, config_entry, "send_message", to=[ALLOWED], body_text="x", attachments=["plan.pdf"])
    assert preview["data"]["preview"]["attachments"] == [{"filename": "plan.pdf", "mime": "application/pdf", "size": 13}]
    (outbox / "plan.pdf").write_bytes(b"%PDF-1.4 other")  # file changed after the preview
    err = await refused(
        hass,
        config_entry,
        "send_message",
        to=[ALLOWED],
        body_text="x",
        attachments=["plan.pdf"],
        confirm_token=preview["data"]["confirm_token"],
    )
    assert err.translation_key == "confirm_token_invalid" and smtp_server.delivered == []


async def test_smtp_failures(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """Rejected login: repair issue + reauth; lost connection during DATA: outcome unknown, counted, not retried."""
    await _setup(hass, config_entry)
    preview = await call(hass, config_entry, "send_message", to=[ALLOWED], body_text="x")
    smtp_server.password = "revoked"
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, config_entry, "send_message", to=[ALLOWED], body_text="x", confirm_token=preview["data"]["confirm_token"])
    assert err.value.translation_key == "smtp_auth_failed"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"smtp_auth_failed_{config_entry.entry_id}") is not None
    assert any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())

    smtp_server.password = mailbox.password
    smtp_server.data_error = smtplib.SMTPServerDisconnected("gone")
    preview = await call(hass, config_entry, "send_message", to=[ALLOWED], body_text="y")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, config_entry, "send_message", to=[ALLOWED], body_text="y", confirm_token=preview["data"]["confirm_token"])
    assert err.value.translation_key == "outcome_unknown"
    assert len([s for s in smtp_server.sessions if s.closed]) == len(smtp_server.sessions)  # no second attempt left open
    log = (await call(hass, config_entry, "get_audit_log"))["data"]["entries"]
    assert log[0]["outcome"] == "unknown"
    status = await call(hass, config_entry, "get_status")
    assert mailbox.mailboxes["Gesendet"].messages == {}  # no Sent copy for an unknown outcome
    # two previews + the sending with an unknown outcome (it may have gone out); the failed login does not count
    assert status["data"]["limits_used"]["actions_today"]["send"] == 3


async def test_sent_copy_modes(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """sent_copy_mode none stores nothing; a failing APPEND is only a warning (the message is out)."""
    await _setup(hass, config_entry, **{CONF_SENT_COPY_MODE: "none"})
    done = await send(hass, config_entry, "send_message", to=[ALLOWED], body_text="x")
    assert done["data"]["sent_copy"] == "none" and mailbox.mailboxes["Gesendet"].messages == {}
    await hass.config_entries.async_unload(config_entry.entry_id)
    hass.config_entries.async_update_entry(config_entry, options={**config_entry.options, CONF_SENT_COPY_MODE: "append"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    del mailbox.mailboxes["Gesendet"]  # APPEND fails
    done = await send(hass, config_entry, "send_message", to=[ALLOWED], body_text="y")
    assert done["data"]["sent_copy"] == "failed" and done["warnings"] == ["sent_copy_failed"]
    assert len(smtp_server.delivered) == 2


@pytest.mark.parametrize("subject", ["Hallo\r\nBcc: evil@example.test", "Hallo\n", "Hallo\rX"])
async def test_subject_must_be_one_line(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer, subject: str
) -> None:
    """Line breaks in the subject are refused with a translated reason (header injection), also a trailing newline."""
    await _setup(hass, config_entry, **{CONF_PERMISSIONS: ["read", "organize", "send"]})
    drafts_before = dict(mailbox.mailboxes["Entwürfe"].messages)
    for service in ("send_message", "save_draft"):
        err = await refused(hass, config_entry, service, to=[ALLOWED], subject=subject, body_text="x")
        assert err.translation_key == "invalid_subject"
    assert smtp_server.delivered == [] and mailbox.mailboxes["Entwürfe"].messages == drafts_before
    log = (await call(hass, config_entry, "get_audit_log"))["data"]["entries"]
    assert log[0]["action"] == "send_message" and log[0]["outcome"] == "rejected" and log[0]["error"] == "invalid_subject"
    status = await call(hass, config_entry, "get_status")
    assert "send" not in status["data"]["limits_used"]["actions_today"]


async def test_refused_send_calls_are_not_counted(
    hass: HomeAssistant, config_entry: MockConfigEntry, mailbox: FakeImapServer, smtp_server: FakeSmtpServer
) -> None:
    """A preview counts once, a sending once; calls refused after composing (allowlist, wrong code) do not count."""
    await _setup(hass, config_entry)
    await refused(hass, config_entry, "send_message", to=["x@fremd.test"], body_text="x")
    await refused(hass, config_entry, "send_message", to=[ALLOWED], body_text="x", confirm_token="falsch")
    await refused(hass, config_entry, "reply", ref=ref(2), body_text="x")  # Reply-To outside the allowlist
    status = await call(hass, config_entry, "get_status")
    assert "send" not in status["data"]["limits_used"]["actions_today"]
    await send(hass, config_entry, "reply", ref=ref(3), body_text="x")
    status = await call(hass, config_entry, "get_status")
    assert status["data"]["limits_used"]["actions_today"]["send"] == 2
