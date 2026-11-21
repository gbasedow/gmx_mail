"""Sensors: unread messages, spam count, last synchronisation, actions today."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change

from .coordinator import GmxMailCoordinator, MailboxData
from .counter import ActionCounter
from .entity import GmxMailEntity, device_info

if TYPE_CHECKING:
    from . import GmxMailConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GmxMailSensorDescription(SensorEntityDescription):
    """Sensor fed by the coordinator."""

    value_fn: Callable[[MailboxData], int | datetime | None]


SENSORS: tuple[GmxMailSensorDescription, ...] = (
    GmxMailSensorDescription(
        key="unseen_inbox",
        translation_key="unseen_inbox",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.unseen_inbox,
    ),
    GmxMailSensorDescription(
        key="junk_messages",
        translation_key="junk_messages",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.junk_messages,
    ),
    GmxMailSensorDescription(
        key="last_sync",
        translation_key="last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_sync,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: GmxMailConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Create the sensors of an account."""
    data = entry.runtime_data
    entities: list[SensorEntity] = [GmxMailSensor(data.coordinator, description) for description in SENSORS]
    entities.append(ActionsTodaySensor(entry, data.counter))
    async_add_entities(entities)


class GmxMailSensor(GmxMailEntity, SensorEntity):
    """Coordinator-based sensor."""

    entity_description: GmxMailSensorDescription

    def __init__(self, coordinator: GmxMailCoordinator, description: GmxMailSensorDescription) -> None:
        """Initialise."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | datetime | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Unavailable while the server is unreachable or the value is unknown (e.g. no spam folder)."""
        return super().available and self.native_value is not None


class ActionsTodaySensor(SensorEntity):
    """Number of actions executed today (diagnostic; reset at local midnight)."""

    _attr_has_entity_name = True
    _attr_translation_key = "actions_today"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(self, entry: GmxMailConfigEntry, counter: ActionCounter) -> None:
        """Initialise."""
        self._counter = counter
        self._attr_unique_id = f"{entry.entry_id}_actions_today"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Follow the counter and refresh at midnight."""
        self.async_on_remove(self._counter.async_add_listener(self.async_write_ha_state))

        @callback
        def _midnight(_now: datetime) -> None:
            self._counter.async_notify()

        self.async_on_remove(async_track_time_change(self.hass, _midnight, hour=0, minute=0, second=1))

    @property
    def native_value(self) -> int:
        """Return today's total."""
        return self._counter.total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return today's counts by permission level."""
        return {"by_level": self._counter.counts}
