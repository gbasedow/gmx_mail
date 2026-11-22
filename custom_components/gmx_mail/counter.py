"""Counters for the limits: actions by level (sensor "actions today"), deleted and sent messages per day, recipients per hour."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORAGE_VERSION = 1
SAVE_DELAY_S = 10


class ActionCounter:
    """Counts executed actions per permission level for the current local day (persisted with ``Store``)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialise; call :meth:`async_load` before use."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.actions")
        self._date = self._today()
        self._counts: dict[str, int] = {}
        self._deleted = 0
        self._sent = 0
        self._recipient_times: list[float] = []  # epoch seconds, one entry per recipient, last hour only
        self._clock: Callable[[], float] = time.time
        self._listeners: list[Callable[[], None]] = []

    @staticmethod
    def _today() -> str:
        return dt_util.now().date().isoformat()

    async def async_load(self) -> None:
        """Restore today's counts (counts of an earlier day are dropped)."""
        stored = await self._store.async_load()
        if stored and stored.get("date") == self._date and isinstance(stored.get("counts"), dict):
            self._counts = {str(k): int(v) for k, v in stored["counts"].items()}
            self._deleted = int(stored.get("deleted", 0))
            self._sent = int(stored.get("sent", 0))
        if stored and isinstance(stored.get("recipient_times"), list):  # the hour window may span midnight
            self._recipient_times = [float(t) for t in stored["recipient_times"] if isinstance(t, int | float)]

    def _roll(self) -> None:
        today = self._today()
        if today != self._date:
            self._date, self._counts, self._deleted, self._sent = today, {}, 0, 0

    @property
    def counts(self) -> dict[str, int]:
        """Return today's counts by level."""
        self._roll()
        return dict(self._counts)

    @property
    def total(self) -> int:
        """Return today's total."""
        return sum(self.counts.values())

    @callback
    def increment(self, level: str) -> None:
        """Count one executed action."""
        self._roll()
        self._counts[level] = self._counts.get(level, 0) + 1
        self._save()
        self.async_notify()

    def _data(self) -> dict[str, Any]:
        return {
            "date": self._date,
            "counts": dict(self._counts),
            "deleted": self._deleted,
            "sent": self._sent,
            "recipient_times": list(self._recipient_times),
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY_S)

    async def async_flush(self) -> None:
        """Write pending counts now (unload/reload): the daily delete limit must not be reset by a reload."""
        await self._store.async_save(self._data())

    @property
    def deleted_today(self) -> int:
        """Return how many messages were moved to the trash or deleted for good today."""
        self._roll()
        return self._deleted

    @property
    def sent_today(self) -> int:
        """Return how many messages were sent today."""
        self._roll()
        return self._sent

    def recipients_last_hour(self) -> int:
        """Return how many recipients were addressed in the last 60 minutes."""
        cutoff = self._clock() - 3600
        self._recipient_times = [t for t in self._recipient_times if t > cutoff]
        return len(self._recipient_times)

    @callback
    def add_sent(self, recipients: int) -> None:
        """Count one sent message and its recipients."""
        self._roll()
        self.recipients_last_hour()
        self._sent += 1
        now = self._clock()
        self._recipient_times.extend([now] * recipients)
        self._save()

    @callback
    def add_deleted(self, count: int) -> None:
        """Count deleted messages towards the daily limit (trash and permanent deletion both count)."""
        if count <= 0:
            return
        self._roll()
        self._deleted += count
        self._save()

    @callback
    def async_notify(self) -> None:
        """Tell listeners (the sensor) that the value may have changed (also used at midnight)."""
        for listener in list(self._listeners):
            listener()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Register a listener; returns a remover."""
        self._listeners.append(listener)

        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    async def async_remove(self) -> None:
        """Delete the stored data (entry removed)."""
        await self._store.async_remove()
