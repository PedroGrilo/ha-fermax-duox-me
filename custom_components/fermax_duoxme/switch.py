"""Switch platform: per-device Auto-on (best-effort).

Auto-on makes the monitor automatically connect to the entrance panel when a
call arrives. The toggle hits api/v2/device/{id}/autoon. Because the exact
request/response shape could not be verified against a live account, the switch
is optimistic: it stores the desired state locally and will surface an error if
the API rejects the call.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import FermaxApiError
from .coordinator import FermaxConfigEntry, FermaxDuoxmeCoordinator
from .entity import FermaxEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FermaxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities = [
        FermaxAutoOnSwitch(coordinator, device.device_id)
        for device in coordinator.data.devices.values()
    ]
    async_add_entities(entities)


class FermaxAutoOnSwitch(FermaxEntity, SwitchEntity):
    """Auto-on toggle for a device."""

    _attr_icon = "mdi:phone-in-talk"
    _attr_entity_registry_enabled_default = False  # best-effort; opt-in

    def __init__(
        self, coordinator: FermaxDuoxmeCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_auto_on"
        self._attr_name = "Auto-on"
        self._state: bool = False

    @property
    def is_on(self) -> bool:
        info = self.coordinator.data.info.get(self._device_id, {})
        if "autoOn" in info:
            return bool(info["autoOn"])
        return self._state

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        try:
            await self.coordinator.client.async_set_auto_on(self._device_id, value)
        except FermaxApiError as err:
            raise HomeAssistantError(f"Failed to set auto-on: {err}") from err
        self._state = value
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
