"""Data update coordinator for Fermax DuoxMe."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AccessDoor,
    FermaxApiError,
    FermaxAuthError,
    FermaxDevice,
    FermaxDuoxmeClient,
    FermaxError,
)
from .const import (
    DOMAIN,
    PHOTO_FETCH_DELAYS,
    PUSH_DEVICE_ID,
    PUSH_TYPE,
    PUSH_TYPE_CALL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class FermaxData:
    """Everything a single refresh produces, keyed by device_id."""

    devices: dict[str, FermaxDevice] = field(default_factory=dict)
    info: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class FermaxPhoto:
    """The photocaller snapshot of one call."""

    photo_id: str
    image: bytes
    taken_at: datetime | None


def parse_call_time(call: dict[str, Any]) -> datetime | None:
    """callDate as a datetime; accepts epoch millis/seconds or ISO strings."""
    raw = call.get("callDate") or call.get("callInstant") or call.get("date")
    if raw is None:
        return None
    if isinstance(raw, str) and raw.isdigit():
        raw = int(raw)
    try:
        if isinstance(raw, (int, float)):
            ts = raw / 1000 if raw > 1e12 else raw
            return datetime.fromtimestamp(ts, tz=dt_util.UTC)
        return dt_util.parse_datetime(str(raw))
    except (ValueError, OSError, OverflowError):
        return None


def signal_ring(entry_id: str, device_id: str) -> str:
    """Dispatcher signal fired when a device's doorbell rings."""
    return f"{DOMAIN}_{entry_id}_{device_id}_ring"


FermaxConfigEntry = ConfigEntry["FermaxDuoxmeCoordinator"]


class FermaxDuoxmeCoordinator(DataUpdateCoordinator[FermaxData]):
    """Polls the DuoxMe cloud."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FermaxConfigEntry,
        client: FermaxDuoxmeClient,
        update_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )
        self.client = client
        # FCM token from the push listener; the call registry is keyed by it.
        self.app_token: str | None = None
        self.photos: dict[str, FermaxPhoto] = {}

    async def _async_update_data(self) -> FermaxData:
        data = FermaxData()
        try:
            devices = await self.client.async_get_devices()
            data.devices = {device.device_id: device for device in devices}
            await asyncio.gather(
                *(self._async_fetch_info(device, data) for device in devices),
                self._async_fetch_calls(data),
            )
        except FermaxAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FermaxApiError as err:
            raise UpdateFailed(str(err)) from err
        return data

    async def _async_fetch_info(self, device: FermaxDevice, data: FermaxData) -> None:
        """Extended info is best-effort; never fail the poll."""
        try:
            info = await self.client.async_get_device_info(device.device_id)
        except FermaxApiError as err:
            _LOGGER.debug("device info failed for %s: %s", device.device_id, err)
        else:
            data.info[device.device_id] = info
            # The pairing payload has no connection state; the Monitor does.
            if "connectionState" in info:
                device.connection_state = info["connectionState"]

    async def _async_fetch_calls(self, data: FermaxData) -> None:
        """Call registry and latest photo per device (needs the push token)."""
        if self.app_token is None:
            return
        try:
            calls = await self.client.async_get_call_registry(self.app_token)
        except FermaxApiError as err:
            _LOGGER.debug("call registry unavailable: %s", err)
            return
        for call in calls:
            data.calls.setdefault(str(call.get("deviceId")), []).append(call)
        for device_id, device_calls in data.calls.items():
            device_calls.sort(
                key=lambda c: parse_call_time(c) or dt_util.utc_from_timestamp(0),
                reverse=True,
            )
            await self._async_fetch_photo(device_id, device_calls)

    async def _async_fetch_photo(
        self, device_id: str, calls: list[dict[str, Any]]
    ) -> None:
        latest = next((c for c in calls if c.get("photoId")), None)
        if latest is None:
            return
        photo_id = str(latest["photoId"])
        cached = self.photos.get(device_id)
        if cached is not None and cached.photo_id == photo_id:
            return
        try:
            image = await self.client.async_get_call_photo(photo_id)
        except FermaxApiError as err:
            _LOGGER.debug("photo %s unavailable: %s", photo_id, err)
            return
        if image:
            self.photos[device_id] = FermaxPhoto(
                photo_id, image, parse_call_time(latest)
            )

    @callback
    def async_handle_push(self, payload: dict[str, Any]) -> None:
        """React to a push from the DuoxMe backend."""
        if payload.get(PUSH_TYPE) != PUSH_TYPE_CALL:
            return
        device_id = str(payload.get(PUSH_DEVICE_ID) or "")
        async_dispatcher_send(
            self.hass, signal_ring(self.config_entry.entry_id, device_id), payload
        )
        # The photo lands in the call registry a little after the ring.
        for delay in PHOTO_FETCH_DELAYS:
            self.config_entry.async_on_unload(
                async_call_later(self.hass, delay, self._async_refresh_later)
            )

    async def _async_refresh_later(self, _now: datetime) -> None:
        await self.async_request_refresh()

    async def async_open_door(self, device_id: str, door: AccessDoor) -> None:
        """Open a door, surfacing failures to the UI."""
        try:
            await self.client.async_open_door(device_id, door.access_id)
        except FermaxError as err:
            raise HomeAssistantError(
                f"Failed to open {door.display_name}: {err}"
            ) from err
