"""Coordinator: mailbox counters, new-mail events and UIDVALIDITY tracking (SPEC section 4).

Updates are pulled every ``poll_interval`` seconds and, with IDLE, additionally whenever the IDLE watcher reports a
change. All IMAP work runs on the work connection (read-only); the IDLE connection only signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from imapclient.exceptions import IMAPClientError

from .client.errors import GmxAuthError, GmxMailError
from .client.folders import FolderMap
from .client.imap import ImapWorker
from .client.reader import PollResult, poll
from .const import (
    CONF_EVENT_INCLUDE_HEADERS,
    CONF_POLL_INTERVAL,
    DOMAIN,
    EVENT_NEW_MESSAGE,
    ISSUE_UIDVALIDITY_CHANGED,
    MAX_NEW_MESSAGE_EVENTS,
    REFRESH_COOLDOWN_S,
    FolderRole,
)

_LOGGER = logging.getLogger(__name__)

STATE_STORAGE_VERSION = 1


@dataclass(frozen=True, slots=True)
class MailboxData:
    """What the sensors show."""

    unseen_inbox: int
    inbox_messages: int
    junk_messages: int | None
    last_sync: datetime


class GmxMailCoordinator(DataUpdateCoordinator[MailboxData]):
    """Polls counters and announces new messages."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, worker: ImapWorker, folder_map: FolderMap, options: dict[str, Any]) -> None:
        """Initialise."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.entry_id}",
            update_interval=timedelta(seconds=int(options[CONF_POLL_INTERVAL])),
            request_refresh_debouncer=Debouncer(hass, _LOGGER, cooldown=REFRESH_COOLDOWN_S, immediate=True),
        )
        self._worker = worker
        self._folder_map = folder_map
        self._include_headers = bool(options.get(CONF_EVENT_INCLUDE_HEADERS))
        self._store: Store[dict[str, Any]] = Store(hass, STATE_STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.state")
        self.inbox = folder_map.get(FolderRole.INBOX) or "INBOX"
        self.last_uid: int | None = None
        self.uidvalidity: int | None = None
        self.new_messages_total = 0
        self.skipped_total = 0
        self.last_error: str | None = None

    async def _async_setup(self) -> None:
        """Restore the last seen UID so that mails received while Home Assistant was down are announced."""
        stored = await self._store.async_load()
        if stored and isinstance(stored.get("last_uid"), int) and isinstance(stored.get("uidvalidity"), int):
            self.last_uid = stored["last_uid"]
            self.uidvalidity = stored["uidvalidity"]

    async def _async_update_data(self) -> MailboxData:
        info = self._worker.server_info
        if info is None:  # pragma: no cover - setup connects before the first refresh
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="cannot_connect")
        job = partial(
            poll,
            info=info,
            inbox=self.inbox,
            junk=self._folder_map.get(FolderRole.JUNK),
            last_uid=self.last_uid,
            known_uidvalidity=self.uidvalidity,
            max_events=MAX_NEW_MESSAGE_EVENTS,
            include_headers=self._include_headers,
        )
        try:
            result: PollResult = await self._worker.async_run(job, readonly=True)
        except GmxAuthError as err:
            self.last_error = "auth_failed"
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth_or_imap_disabled") from err
        except (GmxMailError, IMAPClientError) as err:
            self.last_error = type(err).__name__
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="cannot_connect") from err
        self.last_error = None
        self._handle_result(result)
        return MailboxData(
            unseen_inbox=result.unseen_inbox,
            inbox_messages=result.inbox_messages,
            junk_messages=result.junk_messages,
            last_sync=dt_util.utcnow(),
        )

    def _handle_result(self, result: PollResult) -> None:
        if result.uidvalidity_changed:
            _LOGGER.warning("UIDVALIDITY of the inbox changed; stored mail references are no longer valid")
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{ISSUE_UIDVALIDITY_CHANGED}_{self.config_entry.entry_id}",
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_UIDVALIDITY_CHANGED,
                translation_placeholders={"account": self.config_entry.title, "folder": self.inbox},
            )
        for message in result.new_messages:
            self.hass.bus.async_fire(EVENT_NEW_MESSAGE, {"entry_id": self.config_entry.entry_id, "folder": self.inbox, **message})
        self.new_messages_total += len(result.new_messages)
        if result.skipped_new:
            self.skipped_total += result.skipped_new
            _LOGGER.info(
                "%s further new messages were not announced as events (limit %s per update)", result.skipped_new, MAX_NEW_MESSAGE_EVENTS
            )
        if (result.last_uid, result.uidvalidity) != (self.last_uid, self.uidvalidity):
            self.last_uid, self.uidvalidity = result.last_uid, result.uidvalidity
            self._store.async_delay_save(lambda: {"last_uid": self.last_uid, "uidvalidity": self.uidvalidity}, 5)

    async def async_remove_store(self) -> None:
        """Delete persisted state (entry removed)."""
        await self._store.async_remove()
