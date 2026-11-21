"""Audit log of writing actions (SPEC section 5, measure 8) - without mail content, subjects or addresses.

Entries are kept per account in ``Store`` for ``audit_retention_days`` (at most ``MAX_ENTRIES``) and announced as
event ``gmx_mail_action_executed`` with the same content-free data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_ACTION_EXECUTED

STORAGE_VERSION = 1
MAX_ENTRIES = 5000
SAVE_DELAY_S = 5


def fingerprint(value: Any) -> str:
    """Short, non-reversible hash of the call parameters (lets the user correlate entries without storing content)."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class AuditLog:
    """Ring buffer of executed writing actions for one account."""

    def __init__(self, hass: HomeAssistant, entry_id: str, retention_days: int) -> None:
        """Initialise; call :meth:`async_load` before use."""
        self._hass = hass
        self._entry_id = entry_id
        self._retention = timedelta(days=retention_days)
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.audit")
        self._entries: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        """Load stored entries and drop expired ones."""
        stored = await self._store.async_load()
        if stored and isinstance(stored.get("entries"), list):
            self._entries = [e for e in stored["entries"] if isinstance(e, dict)]
        self._prune()

    def _prune(self) -> None:
        cutoff = (dt_util.utcnow() - self._retention).isoformat()
        self._entries = [e for e in self._entries if str(e.get("time", "")) >= cutoff][-MAX_ENTRIES:]

    @callback
    def record(
        self,
        *,
        action: str,
        level: str,
        outcome: str,
        count: int = 0,
        folders: list[str] | None = None,
        target: str | None = None,
        params: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Store one entry and fire the event. ``params`` is only hashed, never stored."""
        entry: dict[str, Any] = {
            "time": dt_util.utcnow().isoformat(),
            "action": action,
            "level": level,
            "outcome": outcome,
            "count": count,
            "folders": sorted(set(folders or [])),
            "target": target,
            "hash": fingerprint(params),
        }
        if error:
            entry["error"] = error
        self._entries.append(entry)
        self._prune()
        self._store.async_delay_save(lambda: {"entries": list(self._entries)}, SAVE_DELAY_S)
        self._hass.bus.async_fire(EVENT_ACTION_EXECUTED, {"entry_id": self._entry_id, **entry})
        return entry

    def latest(self, limit: int) -> list[dict[str, Any]]:
        """Return the newest entries first."""
        self._prune()
        return list(reversed(self._entries[-limit:]))

    async def async_remove(self) -> None:
        """Delete the stored log (entry removed)."""
        await self._store.async_remove()
