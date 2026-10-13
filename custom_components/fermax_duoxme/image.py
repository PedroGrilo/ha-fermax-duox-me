"""Image platform: photocaller snapshot of the last call."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import FermaxConfigEntry, FermaxDuoxmeCoordinator
from .entity import FermaxEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FermaxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if coordinator.app_token is None:
        # Without the push token the call registry (and photos) can't be read.
        return
    async_add_entities(
        FermaxLastCallImage(coordinator, device.device_id)
        for device in coordinator.data.devices.values()
    )


class FermaxLastCallImage(FermaxEntity, ImageEntity):
    """Photo taken by the panel when the doorbell last rang."""

    _attr_translation_key = "last_call_photo"

    def __init__(
        self, coordinator: FermaxDuoxmeCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id)
        ImageEntity.__init__(self, coordinator.hass)
        self._attr_unique_id = f"{device_id}_last_call_photo"
        self._photo_id: str | None = None
        self._sync_photo()

    @callback
    def _sync_photo(self) -> None:
        photo = self.coordinator.photos.get(self._device_id)
        if photo is None or photo.photo_id == self._photo_id:
            return
        self._photo_id = photo.photo_id
        self._attr_content_type = (
            "image/png" if photo.image.startswith(b"\x89PNG") else "image/jpeg"
        )
        self._attr_image_last_updated = photo.taken_at or dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_photo()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        photo = self.coordinator.photos.get(self._device_id)
        return photo.image if photo else None
