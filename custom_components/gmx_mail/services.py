"""Home Assistant actions of GMX Mail: reading (M2, level R) and organising (M4, level O).

All actions take ``config_entry_id`` in the service data (ha-mcp's ``ha_call_service`` has no ``target``).
Errors are raised as ``ServiceValidationError``/``HomeAssistantError`` with a translation key, never returned
as fields of the response.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from collections.abc import Callable, Coroutine
from functools import partial
from pathlib import Path
from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service
from imapclient.exceptions import IMAPClientError

from .attachments import attachment_dir
from .client.errors import (
    GmxAttachmentTooLargeError,
    GmxAuthError,
    GmxConnectionError,
    GmxConnectionLimitError,
    GmxFolderExistsError,
    GmxFolderNotFoundError,
    GmxInvalidFolderNameError,
    GmxMailError,
    GmxMessageNotFoundError,
    GmxMessageTooLargeError,
    GmxOutcomeUnknownError,
    GmxPartNotFoundError,
    GmxProtectedFolderError,
    GmxSearchRejectedError,
    GmxUidValidityError,
    GmxUnsupportedError,
)
from .client.imap import ImapSession
from .client.reader import MailRef, SearchQuery, get_attachment, get_message, list_folders, search, unseen_count
from .client.sanitize import clean_inline, frame_untrusted, new_nonce
from .client.writer import (
    DraftInput,
    copy_or_move,
    create_folder,
    normalize_flags,
    parse_addresses,
    rename_folder,
    save_draft,
    set_flags,
)
from .const import (
    CONF_ALLOW_HTML_OUTPUT,
    CONF_ALLOWLIST,
    CONF_EMAIL,
    CONF_MAX_DELETE_PER_DAY,
    CONF_MAX_RECIPIENTS,
    CONF_MAX_SEND_PER_DAY,
    CONF_PERMISSIONS,
    CONF_SEND_MODE,
    DOMAIN,
    MAX_REFS_PER_CALL,
    FolderRole,
    Permission,
    PushState,
)
from .envelope import InvalidCursorError, build_envelope, decode_cursor, encode_cursor, fit_list, fit_text, query_hash

_LOGGER = logging.getLogger(__name__)

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SERVICE_LIST_FOLDERS = "list_folders"
SERVICE_SEARCH = "search"
SERVICE_GET_MESSAGE = "get_message"
SERVICE_GET_ATTACHMENT = "get_attachment"
SERVICE_GET_STATUS = "get_status"
SERVICE_GET_AUDIT_LOG = "get_audit_log"
SERVICE_SET_FLAGS = "set_flags"
SERVICE_MOVE = "move"
SERVICE_COPY = "copy"
SERVICE_MARK_SPAM = "mark_spam"
SERVICE_MARK_NOT_SPAM = "mark_not_spam"
SERVICE_CREATE_FOLDER = "create_folder"
SERVICE_RENAME_FOLDER = "rename_folder"
SERVICE_SAVE_DRAFT = "save_draft"
MAX_DRAFT_TEXT = 1_000_000

MAX_TEXT_CRITERION = 200
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
DEFAULT_MAX_CHARS = 8000
MAX_MAX_CHARS = 32000
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_MAX_BYTES = 50 * 1024 * 1024
MAX_BASE64_BYTES = 256 * 1024

_KEYWORD_RE = r"^\$?[A-Za-z0-9_.\-]{1,64}$"
_UID_RANGE_RE = r"^(?:\d+|\*)(?::(?:\d+|\*))?(?:,(?:\d+|\*)(?::(?:\d+|\*))?){0,49}$"
_HEADER_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9\-]{0,63}$"
_PART_ID_RE = r"^[1-9]\d{0,3}(?:\.[1-9]\d{0,3}){0,9}$"

_TEXT = vol.All(cv.string, vol.Length(min=1, max=MAX_TEXT_CRITERION))
_ENTRY = {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
REF_SCHEMA = vol.Schema(
    {
        vol.Required("folder"): vol.All(cv.string, vol.Length(min=1, max=500)),
        vol.Required("uid"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Required("uidvalidity"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)

LIST_FOLDERS_SCHEMA = vol.Schema({**_ENTRY, vol.Optional("include_counts", default=False): cv.boolean})
SEARCH_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Optional("folder", default="INBOX"): vol.All(cv.string, vol.Length(min=1, max=500)),
        vol.Optional("from"): _TEXT,
        vol.Optional("to"): _TEXT,
        vol.Optional("cc"): _TEXT,
        vol.Optional("subject"): _TEXT,
        vol.Optional("text"): _TEXT,
        vol.Optional("body"): _TEXT,
        vol.Optional("since"): cv.date,
        vol.Optional("before"): cv.date,
        vol.Optional("seen"): cv.boolean,
        vol.Optional("flagged"): cv.boolean,
        vol.Optional("answered"): cv.boolean,
        vol.Optional("draft"): cv.boolean,
        vol.Optional("larger"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("smaller"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("keyword"): vol.All(cv.string, vol.Match(_KEYWORD_RE)),
        vol.Optional("uid_range"): vol.All(cv.string, vol.Length(max=500), vol.Match(_UID_RANGE_RE)),
        vol.Optional("sort", default="date_desc"): vol.In(["date_desc", "date_asc"]),
        vol.Optional("limit", default=DEFAULT_LIMIT): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_LIMIT)),
        vol.Optional("cursor"): vol.All(cv.string, vol.Length(max=1024)),
        vol.Optional("include_snippet", default=False): cv.boolean,
    }
)
GET_MESSAGE_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required("ref"): REF_SCHEMA,
        vol.Optional("format", default="text"): vol.In(["text", "html_as_text", "html", "raw_headers"]),
        vol.Optional("max_chars", default=DEFAULT_MAX_CHARS): vol.All(vol.Coerce(int), vol.Range(min=100, max=MAX_MAX_CHARS)),
        vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("headers", default=list): vol.All(cv.ensure_list, vol.Length(max=10), [vol.Match(_HEADER_NAME_RE)]),
    }
)
GET_ATTACHMENT_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required("ref"): REF_SCHEMA,
        vol.Required("part_id"): vol.All(cv.string, vol.Match(_PART_ID_RE)),
        vol.Optional("mode", default="save"): vol.In(["save", "base64"]),
        vol.Optional("max_bytes", default=DEFAULT_MAX_BYTES): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_MAX_BYTES)),
    }
)
GET_STATUS_SCHEMA = vol.Schema(_ENTRY)


# --------------------------------------------------------------------------- helpers


def _entry(hass: HomeAssistant, call: ServiceCall, permission: Permission) -> ConfigEntry:
    entry = service.async_get_config_entry(hass, DOMAIN, call.data[ATTR_CONFIG_ENTRY_ID])
    if permission.value not in entry.runtime_data.options.get(CONF_PERMISSIONS, []):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="permission_disabled", translation_placeholders={"permission": permission.value}
        )
    return entry


# Client error -> (translation key, validation error?). Validation errors are the caller's to fix; the others are
# server or connection problems. Order matters for subclasses (GmxConnectionError before GmxMailError).
_ERROR_KEYS: tuple[tuple[type[Exception], str, bool], ...] = (
    (GmxAuthError, "auth_failed", False),
    (GmxConnectionLimitError, "cannot_connect", False),
    (GmxConnectionError, "cannot_connect", False),
    (GmxOutcomeUnknownError, "outcome_unknown", False),
    (GmxFolderNotFoundError, "folder_not_found", True),
    (GmxUidValidityError, "uidvalidity_changed", True),
    (GmxMessageNotFoundError, "message_not_found", True),
    (GmxPartNotFoundError, "attachment_not_found", True),
    (GmxAttachmentTooLargeError, "attachment_too_large", True),
    (GmxSearchRejectedError, "search_rejected", True),
    (GmxFolderExistsError, "folder_exists", True),
    (GmxProtectedFolderError, "protected_folder", True),
    (GmxMessageTooLargeError, "message_too_large", True),
)


def _translate(err: Exception, folder: str | None = None) -> HomeAssistantError:
    """Map client errors to Home Assistant errors with translation keys (never error fields in responses)."""
    if isinstance(err, GmxUnsupportedError | GmxInvalidFolderNameError):
        return ServiceValidationError(translation_domain=DOMAIN, translation_key=err.reason)
    for error_type, key, validation in _ERROR_KEYS:
        if isinstance(err, error_type):
            if not validation:
                return HomeAssistantError(translation_domain=DOMAIN, translation_key=key)
            placeholders = {"folder": clean_inline(folder or "", 200)} if key == "folder_not_found" else None
            return ServiceValidationError(translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders)
    _LOGGER.debug("IMAP command failed: %s", type(err).__name__)
    return HomeAssistantError(translation_domain=DOMAIN, translation_key="imap_error")


async def _run[T](entry: ConfigEntry, func: Callable[[ImapSession], T], *, folder: str | None = None) -> T:
    """Run a read operation on the work connection (repeated once after a connection loss)."""
    try:
        result: T = await entry.runtime_data.worker.async_run(func, readonly=True)
    except (GmxMailError, IMAPClientError) as err:
        raise _translate(err, folder) from err
    entry.runtime_data.counter.increment(Permission.READ.value)
    return result


async def _write[T](
    entry: ConfigEntry,
    func: Callable[[ImapSession], T],
    *,
    action: str,
    params: Any,
    folder: str | None = None,
    target: str | None = None,
    level: Permission = Permission.ORGANIZE,
) -> T:
    """Run a writing operation (never repeated); failures that may have changed the mailbox are audited."""
    audit = entry.runtime_data.audit
    try:
        result: T = await entry.runtime_data.worker.async_run(func, readonly=False)
    except (GmxMailError, IMAPClientError) as err:
        translated = _translate(err, folder)
        if isinstance(err, GmxOutcomeUnknownError):
            audit.record(action=action, level=level.value, outcome="unknown", target=target, params=params)
        elif not isinstance(translated, ServiceValidationError):
            audit.record(action=action, level=level.value, outcome="error", target=target, params=params, error=type(err).__name__)
        raise translated from err
    entry.runtime_data.counter.increment(level.value)
    return result


def _ref(data: dict[str, Any]) -> MailRef:
    return MailRef(folder=data["folder"], uid=data["uid"], uidvalidity=data["uidvalidity"])


def _safe_filename(name: str | None, uid: int, part_id: str) -> str:
    """Return a file name without path components, control characters or leading dots."""
    base = clean_inline(name or "", 120).replace("/", "_").replace("\\", "_")
    base = re.sub(r"[^\w.\- ()\[\]+,@]", "_", base).strip(" .") or "attachment.bin"
    return f"{uid}_{part_id.replace('.', '-')}_{base}"


def _write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(content)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- handlers


async def _list_folders(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.READ)
    data = entry.runtime_data
    include_counts = call.data["include_counts"]
    folders = await _run(entry, lambda s: list_folders(s, data.server_info, data.folder_map, include_counts=include_counts))
    envelope = build_envelope(account=entry.title, action=SERVICE_LIST_FOLDERS, data={"folders": folders})
    fit_list(envelope, "folders")
    return envelope


def _query_from_call(data: dict[str, Any]) -> SearchQuery:
    return SearchQuery(
        from_=data.get("from"),
        to=data.get("to"),
        cc=data.get("cc"),
        subject=data.get("subject"),
        text=data.get("text"),
        body=data.get("body"),
        since=data.get("since"),
        before=data.get("before"),
        seen=data.get("seen"),
        flagged=data.get("flagged"),
        answered=data.get("answered"),
        draft=data.get("draft"),
        larger=data.get("larger"),
        smaller=data.get("smaller"),
        keyword=data.get("keyword"),
        uid_range=data.get("uid_range"),
    )


async def _search(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.READ)
    info = entry.runtime_data.server_info
    folder: str = call.data["folder"]
    limit: int = call.data["limit"]
    criteria = {k: v for k, v in call.data.items() if k not in (ATTR_CONFIG_ENTRY_ID, "cursor", "limit", "include_snippet")}
    qhash = query_hash(criteria)
    offset, expected_uidvalidity = 0, None
    if call.data.get("cursor"):
        try:
            expected_uidvalidity, offset = decode_cursor(call.data["cursor"], folder=folder, qhash=qhash)
        except InvalidCursorError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_cursor") from err
    query = _query_from_call(dict(call.data))
    result = await _run(
        entry,
        partial(
            _search_job,
            info=info,
            folder=folder,
            query=query,
            order=call.data["sort"],
            limit=limit,
            offset=offset,
            snippet=call.data["include_snippet"],
            expected_uidvalidity=expected_uidvalidity,
        ),
        folder=folder,
    )
    envelope = build_envelope(
        account=entry.title,
        action=SERVICE_SEARCH,
        data={"folder": folder, "search_mode": result.search_mode, "messages": result.messages},
        page={"limit": limit, "offset": offset, "total": result.total, "next_cursor": None},
        warnings=result.warnings,
    )
    shown = fit_list(envelope, "messages")
    next_offset = offset + shown
    if shown < len(result.messages) or next_offset < result.total:
        envelope["page"]["next_cursor"] = encode_cursor(folder=folder, uidvalidity=result.uidvalidity, offset=next_offset, qhash=qhash)
    return envelope


def _search_job(session: ImapSession, **kwargs: Any) -> Any:
    return search(session, **kwargs)


async def _get_message(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.READ)
    if call.data["format"] == "html" and not entry.runtime_data.options.get(CONF_ALLOW_HTML_OUTPUT):
        # A person enables HTML output in the options; a mail (or the model) cannot switch it on.
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="html_output_disabled")
    info = entry.runtime_data.server_info
    ref = _ref(call.data["ref"])
    content = await _run(
        entry,
        lambda s: get_message(s, info, ref, body_format=call.data["format"], extra_headers=call.data["headers"]),
        folder=ref.folder,
    )
    offset: int = call.data["offset"]
    total = len(content.text)
    nonce = new_nonce()
    body: dict[str, Any] = {"format": content.body_format, "chars_total": total, "offset": offset}
    data: dict[str, Any] = {
        "ref": ref.as_dict(),
        "headers": content.headers,
        "flags": content.flags,
        "size": content.size,
        "body": body,
        "attachments": content.attachments,
    }
    if content.auth_results:
        data["auth_results"] = content.auth_results
    if content.list_unsubscribe is not None:
        data["list_unsubscribe"] = content.list_unsubscribe
    envelope = build_envelope(account=entry.title, action=SERVICE_GET_MESSAGE, data=data, warnings=content.warnings)

    def render(n: int) -> None:
        chunk = content.text[offset : offset + n]
        end = offset + len(chunk)
        body["text"] = frame_untrusted(chunk, nonce)
        body["truncated"] = end < total
        body["next_offset"] = end if end < total else None

    fit_text(envelope, render, min(call.data["max_chars"], max(0, total - offset)))
    return envelope


async def _get_attachment(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.READ)
    info = entry.runtime_data.server_info
    ref = _ref(call.data["ref"])
    part_id: str = call.data["part_id"]
    mode: str = call.data["mode"]
    max_bytes: int = min(call.data["max_bytes"], MAX_BASE64_BYTES) if mode == "base64" else call.data["max_bytes"]
    part, content = await _run(entry, lambda s: get_attachment(s, info, ref, part_id, max_bytes=max_bytes), folder=ref.folder)
    digest = hashlib.sha256(content).hexdigest()
    result: dict[str, Any] = {
        "ref": ref.as_dict(),
        "part_id": part_id,
        "filename": part.filename,
        "mime": part.mime,
        "size": len(content),
        "sha256": digest,
    }
    if mode == "base64":
        result["base64"] = base64.b64encode(content).decode("ascii")
    else:
        directory = attachment_dir(hass, entry.entry_id)
        path = directory / _safe_filename(part.filename, ref.uid, part_id)
        if not hass.config.is_allowed_path(str(path)):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="path_not_allowed")
        await hass.async_add_executor_job(_write_file, path, content)
        result["path"] = str(path)
    # A 256 KB attachment is ~350 KB as base64; the 24 KB budget applies to mail text, not to explicit downloads.
    return build_envelope(account=entry.title, action=SERVICE_GET_ATTACHMENT, data=result)


async def _get_status(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.READ)
    data = entry.runtime_data
    inbox = data.folder_map.get(FolderRole.INBOX) or "INBOX"
    unseen = await _run(entry, lambda s: unseen_count(s, inbox), folder=inbox)
    info = data.server_info
    options = data.options
    status = {
        "connected": data.worker.connected,
        "capabilities": sorted(info.capabilities),
        "missing_recommended": info.missing_recommended,
        "append_limit": info.append_limit,
        "folders_map": data.folder_map.as_dict(),
        "unseen_inbox": unseen,
        "idle_active": data.push_state is PushState.CONNECTED,
        "push_state": data.push_state.value,
        "last_sync": data.coordinator.data.last_sync.isoformat() if data.coordinator.data else None,
        "limits_used": {"actions_today": data.counter.counts},
        "permissions": options.get(CONF_PERMISSIONS, []),
        "send_mode": options.get(CONF_SEND_MODE),
        "allowlist_entries": len(options.get(CONF_ALLOWLIST, [])),
        "limits": {
            "max_recipients": options.get(CONF_MAX_RECIPIENTS),
            "max_send_per_day": options.get(CONF_MAX_SEND_PER_DAY),
            "max_delete_per_day": options.get(CONF_MAX_DELETE_PER_DAY),
        },
        "reconnects": data.worker.reconnects,
    }
    return build_envelope(account=entry.title, action=SERVICE_GET_STATUS, data=status)


# --------------------------------------------------------------------------- organising (M4, level O)

_REFS = vol.All(cv.ensure_list, vol.Length(min=1, max=MAX_REFS_PER_CALL), [REF_SCHEMA])
_FLAG_LIST = vol.All(cv.ensure_list, vol.Length(max=10), [vol.All(cv.string, vol.Length(min=1, max=65))])
_FOLDER = vol.All(cv.string, vol.Length(min=1, max=500))
_ADDRESSES = vol.All(cv.ensure_list, vol.Length(max=100), [vol.All(cv.string, vol.Length(min=3, max=320))])

SET_FLAGS_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required("refs"): _REFS, vol.Optional("add", default=list): _FLAG_LIST, vol.Optional("remove", default=list): _FLAG_LIST}
)
MOVE_SCHEMA = vol.Schema({**_ENTRY, vol.Required("refs"): _REFS, vol.Required("target_folder"): _FOLDER})
SPAM_SCHEMA = vol.Schema({**_ENTRY, vol.Required("refs"): _REFS})
CREATE_FOLDER_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=100)), vol.Optional("parent"): _FOLDER}
)
RENAME_FOLDER_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required("path"): _FOLDER, vol.Required("new_name"): vol.All(cv.string, vol.Length(min=1, max=100))}
)
SAVE_DRAFT_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Optional("to", default=list): _ADDRESSES,
        vol.Optional("cc", default=list): _ADDRESSES,
        vol.Optional("bcc", default=list): _ADDRESSES,
        vol.Optional("subject"): vol.All(cv.string, vol.Length(max=998)),
        vol.Optional("body_text", default=""): vol.All(cv.string, vol.Length(max=MAX_DRAFT_TEXT)),
        vol.Optional("body_html"): vol.All(cv.string, vol.Length(max=MAX_DRAFT_TEXT)),
        vol.Optional("in_reply_to_ref"): REF_SCHEMA,
    }
)
GET_AUDIT_LOG_SCHEMA = vol.Schema({**_ENTRY, vol.Optional("limit", default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=200))})


def _refs(call: ServiceCall) -> list[MailRef]:
    refs = [_ref(item) for item in call.data["refs"]]
    return list(dict.fromkeys(refs))  # duplicates would be moved/flagged twice


def _write_envelope(entry: ConfigEntry, action: str, data: dict[str, Any], warnings: list[str] | None = None) -> ServiceResponse:
    return build_envelope(account=entry.title, action=action, data=data, warnings=warnings)


async def _set_flags(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.ORGANIZE)
    add, remove = call.data["add"], call.data["remove"]
    if not add and not remove:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_flags")
    try:
        normalize_flags([*add, *remove])
    except ValueError as err:
        # Only seen, flagged, answered and keywords: \Deleted belongs to the delete level (M5), \Draft/\Recent to the server.
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_flag") from err
    info, refs = entry.runtime_data.server_info, _refs(call)
    params = {"refs": [r.as_dict() for r in refs], "add": add, "remove": remove}
    result = await _write(entry, lambda s: set_flags(s, info, refs, add, remove), action=SERVICE_SET_FLAGS, params=params)
    entry.runtime_data.audit.record(
        action=SERVICE_SET_FLAGS, level=Permission.ORGANIZE.value, outcome="ok", count=result.count, folders=result.folders, params=params
    )
    return _write_envelope(entry, SERVICE_SET_FLAGS, {"updated": result.count, "missing": result.missing})


async def _transfer(hass: HomeAssistant, call: ServiceCall, action: str, target: str, *, move: bool) -> ServiceResponse:
    entry = _entry(hass, call, Permission.ORGANIZE)
    info, refs = entry.runtime_data.server_info, _refs(call)
    params = {"refs": [r.as_dict() for r in refs], "target": target}
    result = await _write(
        entry, lambda s: copy_or_move(s, info, refs, target, move=move), action=action, params=params, folder=target, target=target
    )
    entry.runtime_data.audit.record(
        action=action,
        level=Permission.ORGANIZE.value,
        outcome="ok",
        count=result.count,
        folders=result.folders,
        target=target,
        params=params,
    )
    key = "moved" if move else "copied"
    data = {key: result.count, "target_folder": target, "new_refs": result.new_refs, "missing": result.missing}
    return _write_envelope(entry, action, data, result.warnings)


async def _move(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    return await _transfer(hass, call, SERVICE_MOVE, call.data["target_folder"], move=True)


async def _copy(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    return await _transfer(hass, call, SERVICE_COPY, call.data["target_folder"], move=False)


async def _mark_spam(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.ORGANIZE)
    junk = entry.runtime_data.folder_map.get(FolderRole.JUNK)
    if not junk:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_junk_folder")
    return await _transfer(hass, call, SERVICE_MARK_SPAM, junk, move=True)


async def _mark_not_spam(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    # Always back to the inbox: IMAP does not remember the folder a message came from (DECISIONS Nr. 39).
    entry = _entry(hass, call, Permission.ORGANIZE)
    inbox = entry.runtime_data.folder_map.get(FolderRole.INBOX) or "INBOX"
    return await _transfer(hass, call, SERVICE_MARK_NOT_SPAM, inbox, move=True)


async def _create_folder(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.ORGANIZE)
    data = entry.runtime_data
    name, parent = call.data["name"], call.data.get("parent")
    params = {"name": name, "parent": parent}
    path = await _write(
        entry,
        lambda s: create_folder(s, data.server_info, data.folder_map, name, parent),
        action=SERVICE_CREATE_FOLDER,
        params=params,
        folder=parent,
    )
    data.audit.record(action=SERVICE_CREATE_FOLDER, level=Permission.ORGANIZE.value, outcome="ok", count=1, target=path, params=params)
    return _write_envelope(entry, SERVICE_CREATE_FOLDER, {"path": path})


async def _rename_folder(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.ORGANIZE)
    data = entry.runtime_data
    path, new_name = call.data["path"], call.data["new_name"]
    params = {"path": path, "new_name": new_name}
    new_path = await _write(
        entry,
        lambda s: rename_folder(s, data.server_info, data.folder_map, path, new_name),
        action=SERVICE_RENAME_FOLDER,
        params=params,
        folder=path,
    )
    data.audit.record(
        action=SERVICE_RENAME_FOLDER, level=Permission.ORGANIZE.value, outcome="ok", count=1, folders=[path], target=new_path, params=params
    )
    return _write_envelope(entry, SERVICE_RENAME_FOLDER, {"path": new_path, "previous_path": path})


async def _save_draft(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.ORGANIZE)
    data = entry.runtime_data
    try:
        for field_name in ("to", "cc", "bcc"):
            parse_addresses(call.data[field_name])
    except ValueError as err:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_address") from err
    reply_ref = _ref(call.data["in_reply_to_ref"]) if call.data.get("in_reply_to_ref") else None
    draft = DraftInput(
        sender=str(entry.data[CONF_EMAIL]),
        to=call.data["to"],
        cc=call.data["cc"],
        bcc=call.data["bcc"],
        subject=call.data.get("subject"),
        body_text=call.data["body_text"],
        body_html=call.data.get("body_html"),
        in_reply_to=reply_ref,
    )
    drafts = data.folder_map.get(FolderRole.DRAFTS)
    # Only the shape of the call is hashed; recipients and text are never stored.
    params = {"recipients": len(draft.to) + len(draft.cc) + len(draft.bcc), "reply": reply_ref.as_dict() if reply_ref else None}
    ref, size = await _write(
        entry, lambda s: save_draft(s, data.server_info, data.folder_map, draft), action=SERVICE_SAVE_DRAFT, params=params, target=drafts
    )
    data.audit.record(action=SERVICE_SAVE_DRAFT, level=Permission.ORGANIZE.value, outcome="ok", count=1, target=drafts, params=params)
    return _write_envelope(entry, SERVICE_SAVE_DRAFT, {"ref": ref, "size": size}, None if ref else ["new_refs_incomplete"])


async def _get_audit_log(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call, Permission.READ)
    entries = entry.runtime_data.audit.latest(call.data["limit"])
    entry.runtime_data.counter.increment(Permission.READ.value)
    envelope = build_envelope(account=entry.title, action=SERVICE_GET_AUDIT_LOG, data={"entries": entries})
    fit_list(envelope, "entries")
    return envelope


# --------------------------------------------------------------------------- registration

type _Handler = Callable[[HomeAssistant, ServiceCall], Coroutine[Any, Any, ServiceResponse]]

_READ_HANDLERS: dict[str, tuple[_Handler, vol.Schema]] = {
    SERVICE_LIST_FOLDERS: (_list_folders, LIST_FOLDERS_SCHEMA),
    SERVICE_SEARCH: (_search, SEARCH_SCHEMA),
    SERVICE_GET_MESSAGE: (_get_message, GET_MESSAGE_SCHEMA),
    SERVICE_GET_ATTACHMENT: (_get_attachment, GET_ATTACHMENT_SCHEMA),
    SERVICE_GET_STATUS: (_get_status, GET_STATUS_SCHEMA),
    SERVICE_GET_AUDIT_LOG: (_get_audit_log, GET_AUDIT_LOG_SCHEMA),
}
_WRITE_HANDLERS: dict[str, tuple[_Handler, vol.Schema]] = {
    SERVICE_SET_FLAGS: (_set_flags, SET_FLAGS_SCHEMA),
    SERVICE_MOVE: (_move, MOVE_SCHEMA),
    SERVICE_COPY: (_copy, MOVE_SCHEMA),
    SERVICE_MARK_SPAM: (_mark_spam, SPAM_SCHEMA),
    SERVICE_MARK_NOT_SPAM: (_mark_not_spam, SPAM_SCHEMA),
    SERVICE_CREATE_FOLDER: (_create_folder, CREATE_FOLDER_SCHEMA),
    SERVICE_RENAME_FOLDER: (_rename_folder, RENAME_FOLDER_SCHEMA),
    SERVICE_SAVE_DRAFT: (_save_draft, SAVE_DRAFT_SCHEMA),
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register all actions: reading ones must be called with a response, writing ones may be."""
    for handlers, supports in ((_READ_HANDLERS, SupportsResponse.ONLY), (_WRITE_HANDLERS, SupportsResponse.OPTIONAL)):
        for name, (handler, schema) in handlers.items():
            hass.services.async_register(DOMAIN, name, partial(handler, hass), schema=schema, supports_response=supports)
