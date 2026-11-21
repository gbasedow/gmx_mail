"""Home Assistant actions of GMX Mail (M2: read actions).

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

from .client.errors import (
    GmxAttachmentTooLargeError,
    GmxAuthError,
    GmxConnectionError,
    GmxConnectionLimitError,
    GmxFolderNotFoundError,
    GmxMailError,
    GmxMessageNotFoundError,
    GmxOutcomeUnknownError,
    GmxPartNotFoundError,
    GmxSearchRejectedError,
    GmxUidValidityError,
)
from .client.imap import ImapSession
from .client.reader import MailRef, SearchQuery, get_attachment, get_message, list_folders, search, unseen_count
from .client.sanitize import clean_inline, frame_untrusted, new_nonce
from .const import (
    CONF_ALLOW_HTML_OUTPUT,
    CONF_ALLOWLIST,
    CONF_MAX_DELETE_PER_DAY,
    CONF_MAX_RECIPIENTS,
    CONF_MAX_SEND_PER_DAY,
    CONF_PERMISSIONS,
    CONF_SEND_MODE,
    DOMAIN,
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

MAX_TEXT_CRITERION = 200
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
DEFAULT_MAX_CHARS = 8000
MAX_MAX_CHARS = 32000
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_MAX_BYTES = 50 * 1024 * 1024
MAX_BASE64_BYTES = 256 * 1024
MEDIA_SUBDIR = "gmx_mail"

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


async def _run[T](entry: ConfigEntry, func: Callable[[ImapSession], T], *, folder: str | None = None) -> T:
    """Run a read operation on the work connection and translate errors."""
    placeholders = {"folder": clean_inline(folder, 200)} if folder else None
    try:
        result: T = await entry.runtime_data.worker.async_run(func, readonly=True)
    except GmxAuthError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
    except (GmxConnectionError, GmxConnectionLimitError) as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="cannot_connect") from err
    except GmxOutcomeUnknownError as err:  # pragma: no cover - reads are retried, never "unknown"
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="cannot_connect") from err
    except GmxFolderNotFoundError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="folder_not_found", translation_placeholders=placeholders
        ) from err
    except GmxUidValidityError as err:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="uidvalidity_changed") from err
    except GmxMessageNotFoundError as err:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="message_not_found") from err
    except GmxPartNotFoundError as err:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="attachment_not_found") from err
    except GmxAttachmentTooLargeError as err:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="attachment_too_large") from err
    except GmxSearchRejectedError as err:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="search_rejected") from err
    except (GmxMailError, IMAPClientError) as err:
        _LOGGER.debug("IMAP command failed: %s", type(err).__name__)
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="imap_error") from err
    entry.runtime_data.counter.increment(Permission.READ.value)
    return result


def _ref(data: dict[str, Any]) -> MailRef:
    return MailRef(folder=data["folder"], uid=data["uid"], uidvalidity=data["uidvalidity"])


def _safe_filename(name: str | None, uid: int, part_id: str) -> str:
    """Return a file name without path components, control characters or leading dots."""
    base = clean_inline(name or "", 120).replace("/", "_").replace("\\", "_")
    base = re.sub(r"[^\w.\- ()\[\]+,@]", "_", base).strip(" .") or "attachment.bin"
    return f"{uid}_{part_id.replace('.', '-')}_{base}"


def _media_root(hass: HomeAssistant) -> Path:
    return Path(hass.config.media_dirs.get("local") or hass.config.path("media"))


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
        directory = _media_root(hass) / MEDIA_SUBDIR / entry.entry_id
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


# --------------------------------------------------------------------------- registration

_HANDLERS: dict[str, tuple[Callable[[HomeAssistant, ServiceCall], Coroutine[Any, Any, ServiceResponse]], vol.Schema]] = {
    SERVICE_LIST_FOLDERS: (_list_folders, LIST_FOLDERS_SCHEMA),
    SERVICE_SEARCH: (_search, SEARCH_SCHEMA),
    SERVICE_GET_MESSAGE: (_get_message, GET_MESSAGE_SCHEMA),
    SERVICE_GET_ATTACHMENT: (_get_attachment, GET_ATTACHMENT_SCHEMA),
    SERVICE_GET_STATUS: (_get_status, GET_STATUS_SCHEMA),
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register all actions (read-only actions only return data)."""
    for name, (handler, schema) in _HANDLERS.items():
        hass.services.async_register(
            DOMAIN,
            name,
            partial(handler, hass),
            schema=schema,
            supports_response=SupportsResponse.ONLY,
        )
