"""Per-day action counter (diagnostic sensor "actions today"; later the base for the daily limits, M4-M6)."""

from __future__ import annotations

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
        self._listeners: list[Callable[[], None]] = []

    @staticmethod
    def _today() -> str:
        return dt_util.now().date().isoformat()

    async def async_load(self) -> None:
        """Restore today's counts (counts of an earlier day are dropped)."""
        stored = await self._store.async_load()
        if stored and stored.get("date") == self._date and isinstance(stored.get("counts"), dict):
            self._counts = {str(k): int(v) for k, v in stored["counts"].items()}

    def _roll(self) -> None:
        today = self._today()
        if today != self._date:
            self._date, self._counts = today, {}

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
        self._store.async_delay_save(lambda: {"date": self._date, "counts": dict(self._counts)}, SAVE_DELAY_S)
        self.async_notify()

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
