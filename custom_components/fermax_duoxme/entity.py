"""Shared base entity for Fermax DuoxMe."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import FermaxDevice
from .const import ATTRIBUTION, DOMAIN
from .coordinator import FermaxDuoxmeCoordinator


class FermaxEntity(CoordinatorEntity[FermaxDuoxmeCoordinator]):
    """Base entity tied to one DuoxMe device."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self, coordinator: FermaxDuoxmeCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id

    @property
    def device(self) -> FermaxDevice | None:
        return self.coordinator.data.devices.get(self._device_id)

    @property
    def available(self) -> bool:
        return super().available and self.device is not None

    @property
    def device_info(self) -> DeviceInfo:
        device = self.device
        info = self.coordinator.data.info.get(self._device_id, {})
        name = device.name if device else self._device_id
        # Monitor model fields: subtype, blueStream, photocaller, wirelessSignal.
        model = info.get("subtype") or info.get("subType") or "DuoxMe"
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=name,
            manufacturer="Fermax",
            model=str(model),
        )
