"""Binary sensor platform: device connectivity."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FermaxConfigEntry, FermaxDuoxmeCoordinator
from .entity import FermaxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FermaxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities = [
        FermaxConnectivity(coordinator, device.device_id)
        for device in coordinator.data.devices.values()
    ]
    async_add_entities(entities)


class FermaxConnectivity(FermaxEntity, BinarySensorEntity):
    """Whether the device reports itself as connected to the cloud."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connectivity"

    def __init__(
        self, coordinator: FermaxDuoxmeCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_connectivity"

    @property
    def available(self) -> bool:
        # Report the connectivity sensor itself as available as long as the
        # device exists in the last poll, even if it is "disconnected".
        return self.coordinator.last_update_success and self.device is not None

    @property
    def is_on(self) -> bool | None:
        device = self.device
        return device.available if device else None
