"""Diagnostics download (SPEC section 4): no password, no addresses, no subjects, no mail content."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ALLOWLIST, CONF_EMAIL, CONF_PASSWORD

if TYPE_CHECKING:
    from . import GmxMailConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "title", "unique_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GmxMailConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    options = {key: value for key, value in data.options.items() if key != CONF_ALLOWLIST}
    options["allowlist_entries"] = len(data.options.get(CONF_ALLOWLIST, []))
    coordinator = data.coordinator
    mailbox = coordinator.data
    idle = data.idle
    return {
        "entry": async_redact_data({"title": entry.title, "unique_id": entry.unique_id, "data": dict(entry.data)}, TO_REDACT),
        "options": options,
        "server": {
            "capabilities": sorted(data.server_info.capabilities),
            "missing_recommended": data.server_info.missing_recommended,
            "append_limit": data.server_info.append_limit,
        },
        "folders": data.folder_map.as_dict(),
        "work_connection": {
            "connected": data.worker.connected,
            "reconnects": data.worker.reconnects,
            "auth_failed": data.worker.auth_failed,
            "connections_in_use": data.limiter.in_use,
        },
        "push": {
            "state": data.push_state.value,
            "reconnects": idle.reconnects if idle else 0,
            "last_error": idle.last_error if idle else None,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_error": coordinator.last_error,
            "unseen_inbox": mailbox.unseen_inbox if mailbox else None,
            "inbox_messages": mailbox.inbox_messages if mailbox else None,
            "junk_messages": mailbox.junk_messages if mailbox else None,
            "last_sync": mailbox.last_sync.isoformat() if mailbox else None,
            "new_messages_announced": coordinator.new_messages_total,
            "new_messages_skipped": coordinator.skipped_total,
        },
        "actions_today": data.counter.counts,
    }
