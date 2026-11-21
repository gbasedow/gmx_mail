"""Base entity: one service device per account."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GmxMailCoordinator


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the device of an account."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="GMX",
        model="IMAP/SMTP",
        entry_type=DeviceEntryType.SERVICE,
    )


class GmxMailEntity(CoordinatorEntity[GmxMailCoordinator]):
    """Entity bound to the account coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GmxMailCoordinator, key: str) -> None:
        """Initialise with a translation key that also forms the unique id."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)
