"""Lock platform: one lock per openable door.

The door relay is momentary — there is no real lock state to read back — so the
lock shows "unlocked" briefly after an open pulse and then relocks itself.
Use these for voice assistants / HomeKit ("unlock the front door"). The button
platform exposes the same doors with cleaner momentary semantics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .api import AccessDoor, FermaxApiError
from .const import LOCK_RELOCK_DELAY
from .coordinator import FermaxConfigEntry, FermaxDuoxmeCoordinator
from .entity import FermaxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FermaxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[FermaxDoorLock] = []
    for device in coordinator.data.devices.values():
        for door in device.doors:
            # Hidden doors still get an entity, but disabled by default.
            entities.append(FermaxDoorLock(coordinator, device.device_id, door))
    async_add_entities(entities)


class FermaxDoorLock(FermaxEntity, LockEntity):
    """A door exposed as a lock."""

    def __init__(
        self,
        coordinator: FermaxDuoxmeCoordinator,
        device_id: str,
        door: AccessDoor,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._door = door
        self._attr_unique_id = f"{device_id}_{door.key}_lock"
        self._attr_name = door.display_name
        self._attr_entity_registry_enabled_default = door.visible
        self._attr_is_locked = True
        self._unsub_relock = None

    @property
    def is_locked(self) -> bool | None:
        return self._attr_is_locked

    async def async_unlock(self, **kwargs: Any) -> None:
        """Open the door (momentary)."""
        try:
            await self.coordinator.client.async_open_door(
                self._device_id, self._door.access_id
            )
        except FermaxApiError as err:
            raise HomeAssistantError(f"Failed to open {self._door.title}: {err}") from err

        self._attr_is_locked = False
        self.async_write_ha_state()

        if self._unsub_relock is not None:
            self._unsub_relock()

        @callback
        def _relock(_now: datetime) -> None:
            self._attr_is_locked = True
            self._unsub_relock = None
            self.async_write_ha_state()

        self._unsub_relock = async_call_later(
            self.hass, LOCK_RELOCK_DELAY, _relock
        )

    async def async_lock(self, **kwargs: Any) -> None:
        """No physical lock action; just reflect the locked state."""
        self._attr_is_locked = True
        self.async_write_ha_state()
