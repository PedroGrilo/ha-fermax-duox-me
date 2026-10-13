"""Button platform: one 'open door' button per openable door (momentary)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AccessDoor, FermaxApiError
from .coordinator import FermaxConfigEntry, FermaxDuoxmeCoordinator
from .entity import FermaxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FermaxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = []
    for device in coordinator.data.devices.values():
        for door in device.doors:
            # Hidden doors still get a button, but disabled by default.
            entities.append(
                FermaxOpenDoorButton(coordinator, device.device_id, door)
            )
        entities.append(FermaxAutoOnButton(coordinator, device.device_id))
    async_add_entities(entities)


class FermaxOpenDoorButton(FermaxEntity, ButtonEntity):
    """Press to open a specific door."""

    _attr_icon = "mdi:door-open"

    def __init__(
        self,
        coordinator: FermaxDuoxmeCoordinator,
        device_id: str,
        door: AccessDoor,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._door = door
        self._attr_unique_id = f"{device_id}_{door.key}_open"
        self._attr_name = f"Open {door.display_name}"
        self._attr_entity_registry_enabled_default = door.visible

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.async_open_door(
                self._device_id, self._door.access_id
            )
        except FermaxApiError as err:
            raise HomeAssistantError(
                f"Failed to open {self._door.title}: {err}"
            ) from err


class FermaxAutoOnButton(FermaxEntity, ButtonEntity):
    """Trigger auto-on (auto-answer) on the device. Best-effort; opt-in."""

    _attr_icon = "mdi:phone-in-talk"
    _attr_translation_key = "auto_on"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: FermaxDuoxmeCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_auto_on"
        self._attr_name = "Auto-on"

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.async_trigger_auto_on(self._device_id)
        except FermaxApiError as err:
            raise HomeAssistantError(f"Failed to trigger auto-on: {err}") from err
