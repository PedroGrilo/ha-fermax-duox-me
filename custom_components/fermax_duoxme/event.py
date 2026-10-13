"""Event platform: doorbell rings, pushed in real time."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PUSH_ACCESS_DOOR, PUSH_ROOM_ID
from .coordinator import FermaxConfigEntry, FermaxDuoxmeCoordinator, signal_ring
from .entity import FermaxEntity

EVENT_RING = "ring"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FermaxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if coordinator.app_token is None:
        # Push isn't running, so there is nothing to report rings from.
        return
    async_add_entities(
        FermaxDoorbell(coordinator, device.device_id)
        for device in coordinator.data.devices.values()
    )


class FermaxDoorbell(FermaxEntity, EventEntity):
    """Fires 'ring' when someone calls from the street panel."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [EVENT_RING]
    _attr_translation_key = "doorbell"

    def __init__(
        self, coordinator: FermaxDuoxmeCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_doorbell"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_ring(self.coordinator.config_entry.entry_id, self._device_id),
                self._async_ring,
            )
        )

    @callback
    def _async_ring(self, payload: dict[str, Any]) -> None:
        self._trigger_event(
            EVENT_RING,
            {
                "access_door": payload.get(PUSH_ACCESS_DOOR),
                "room_id": payload.get(PUSH_ROOM_ID),
            },
        )
        self.async_write_ha_state()
