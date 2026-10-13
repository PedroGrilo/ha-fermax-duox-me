"""The Fermax DuoxMe integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FermaxDuoxmeClient, FermaxError, FermaxTokens
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_FCM_CREDENTIALS,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
)
from .coordinator import FermaxConfigEntry, FermaxDuoxmeCoordinator
from .push import (
    FermaxPushListener,
    installation_id,
    push_supported,
    registration_token,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LOCK,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.IMAGE,
]


async def async_setup_entry(hass: HomeAssistant, entry: FermaxConfigEntry) -> bool:
    """Set up Fermax DuoxMe from a config entry."""

    @callback
    def _async_save_tokens(new_tokens: FermaxTokens) -> None:
        """Persist refreshed tokens so they survive a restart."""
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: new_tokens.access_token,
                CONF_REFRESH_TOKEN: new_tokens.refresh_token,
                CONF_TOKEN_EXPIRES_AT: new_tokens.expires_at,
            },
        )

    client = _client_from_entry(hass, entry, _async_save_tokens)
    coordinator = FermaxDuoxmeCoordinator(
        hass, entry, client, _update_interval(entry)
    )

    # Push gives real-time rings and the token the call registry is keyed by.
    # It is optional: without it the doors, sensors and buttons still work.
    if push_supported():
        push = FermaxPushListener(hass, entry, client, coordinator.async_handle_push)
        try:
            await push.async_start()
        except Exception:  # noqa: BLE001 - FCM/library errors vary
            _LOGGER.warning(
                "Doorbell push notifications unavailable; ring events and "
                "call photos are disabled",
                exc_info=True,
            )
            await push.async_stop()
        else:
            coordinator.app_token = push.app_token
            entry.async_on_unload(push.async_stop)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FermaxConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: FermaxConfigEntry) -> None:
    """Drop Home Assistant's push token so it stops counting as a phone."""
    credentials = entry.data.get(CONF_FCM_CREDENTIALS)
    fid = installation_id(credentials)
    token = registration_token(credentials)
    if fid is None or token is None:
        return
    try:
        await _client_from_entry(hass, entry).async_unregister_app_token(fid, token)
    except FermaxError as err:
        _LOGGER.warning("Could not unregister the push token: %s", err)


def _client_from_entry(
    hass: HomeAssistant,
    entry: FermaxConfigEntry,
    on_tokens_refreshed: Callable[[FermaxTokens], None] | None = None,
) -> FermaxDuoxmeClient:
    tokens = FermaxTokens(
        access_token=entry.data[CONF_ACCESS_TOKEN],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        expires_at=entry.data.get(CONF_TOKEN_EXPIRES_AT, 0.0),
    )
    return FermaxDuoxmeClient(
        async_get_clientsession(hass), tokens, on_tokens_refreshed=on_tokens_refreshed
    )


def _update_interval(entry: FermaxConfigEntry) -> int:
    return entry.options.get(
        CONF_UPDATE_INTERVAL,
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
    )


async def _async_update_listener(
    hass: HomeAssistant, entry: FermaxConfigEntry
) -> None:
    """Apply a new polling interval in place.

    This also fires on every token save, so it must not reload the entry.
    """
    entry.runtime_data.update_interval = timedelta(seconds=_update_interval(entry))
