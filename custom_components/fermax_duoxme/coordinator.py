"""Data update coordinator for Fermax DuoxMe."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FermaxApiError, FermaxAuthError, FermaxDevice, FermaxDuoxmeClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class FermaxData:
    """Everything a single refresh produces, keyed by device_id."""

    devices: dict[str, FermaxDevice] = field(default_factory=dict)
    info: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


FermaxConfigEntry = ConfigEntry["FermaxDuoxmeCoordinator"]


class FermaxDuoxmeCoordinator(DataUpdateCoordinator[FermaxData]):
    """Polls the DuoxMe cloud and keeps tokens fresh in the config entry."""

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
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> FermaxData:
        try:
            devices = await self.client.async_get_devices()
        except FermaxAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FermaxApiError as err:
            raise UpdateFailed(str(err)) from err

        data = FermaxData()
        for device in devices:
            data.devices[device.device_id] = device
            # Extended info and call registry are best-effort; never fail the poll.
            try:
                data.info[device.device_id] = await self.client.async_get_device_info(
                    device.device_id
                )
            except FermaxApiError as err:
                _LOGGER.debug("device info failed for %s: %s", device.device_id, err)
            data.calls[device.device_id] = await self.client.async_get_call_registry(
                device.device_id
            )

        # Persist any refreshed tokens back into the config entry.
        self._save_tokens()
        return data

    def _save_tokens(self) -> None:
        tokens = self.client.tokens
        if not tokens:
            return
        from .const import (  # local import avoids cycle at module load
            CONF_ACCESS_TOKEN,
            CONF_REFRESH_TOKEN,
            CONF_TOKEN_EXPIRES_AT,
        )

        current = self.entry.data
        if (
            current.get(CONF_ACCESS_TOKEN) == tokens.access_token
            and current.get(CONF_REFRESH_TOKEN) == tokens.refresh_token
        ):
            return
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **current,
                CONF_ACCESS_TOKEN: tokens.access_token,
                CONF_REFRESH_TOKEN: tokens.refresh_token,
                CONF_TOKEN_EXPIRES_AT: tokens.expires_at,
            },
        )
