"""Sensor platform: connection state, last call, wifi signal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FermaxConfigEntry, FermaxDuoxmeCoordinator, parse_call_time
from .entity import FermaxEntity


def _last_call_time(coordinator: FermaxDuoxmeCoordinator, device_id: str) -> Any:
    # Calls are sorted newest first by the coordinator.
    calls = coordinator.data.calls.get(device_id)
    return parse_call_time(calls[0]) if calls else None


@dataclass(frozen=True, kw_only=True)
class FermaxSensorDescription(SensorEntityDescription):
    """Describes a Fermax sensor."""

    value_fn: Callable[[FermaxDuoxmeCoordinator, str], Any]


SENSORS: tuple[FermaxSensorDescription, ...] = (
    FermaxSensorDescription(
        key="connection_state",
        translation_key="connection_state",
        icon="mdi:lan-connect",
        value_fn=lambda c, d: (
            dev.connection_state
            if (dev := c.data.devices.get(d)) is not None
            else None
        ),
    ),
    FermaxSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c, d: c.data.info.get(d, {}).get("wirelessSignal"),
    ),
    FermaxSensorDescription(
        key="last_call",
        translation_key="last_call",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:phone-clock",
        value_fn=_last_call_time,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FermaxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities = [
        FermaxSensor(coordinator, device.device_id, desc)
        for device in coordinator.data.devices.values()
        for desc in SENSORS
    ]
    async_add_entities(entities)


class FermaxSensor(FermaxEntity, SensorEntity):
    """Generic read-only sensor driven by a value_fn."""

    entity_description: FermaxSensorDescription

    def __init__(
        self,
        coordinator: FermaxDuoxmeCoordinator,
        device_id: str,
        description: FermaxSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator, self._device_id)
