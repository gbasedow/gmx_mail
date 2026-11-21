"""Binary sensor: connection to the mail server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import GmxMailEntity

if TYPE_CHECKING:
    from . import GmxMailConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: GmxMailConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Create the connection sensor."""
    async_add_entities([ConnectionSensor(entry)])


class ConnectionSensor(GmxMailEntity, BinarySensorEntity):
    """On while the last update succeeded; the push (IDLE) state is shown as attribute."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: GmxMailConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry.runtime_data.coordinator, "connection")
        self._entry = entry

    @property
    def available(self) -> bool:
        """Always available: 'off' is the information."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the server was reachable at the last update."""
        return self.coordinator.last_update_success and not self._entry.runtime_data.worker.auth_failed

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the push state (connected, reconnecting, disabled, unsupported, ...)."""
        return {"push": self._entry.runtime_data.push_state.value}
