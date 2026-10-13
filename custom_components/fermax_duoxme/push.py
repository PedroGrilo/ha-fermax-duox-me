"""Real-time doorbell notifications via Firebase Cloud Messaging.

Home Assistant registers itself with the DuoxMe backend as one more "phone": it
obtains an FCM token from the app's Firebase project, subscribes that token
(POST /notification/api/v2/apptoken) and keeps a push connection open. The same
token is what the call registry -- and so the photocaller photos -- is keyed by.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from firebase_messaging import FcmPushClient, FcmRegisterConfig

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FermaxDuoxmeClient
from .const import (
    CONF_FCM_CREDENTIALS,
    CONF_FCM_PERSISTENT_IDS,
    FCM_API_KEY,
    FCM_APP_ID,
    FCM_PROJECT_ID,
    FCM_SENDER_ID,
)

_LOGGER = logging.getLogger(__name__)

# How many delivered message ids to remember, so a restart doesn't replay them.
_MAX_PERSISTENT_IDS = 50


def push_supported() -> bool:
    """Push needs the app's Firebase API key (see const.FCM_API_KEY)."""
    return bool(FCM_API_KEY)


def installation_id(credentials: dict[str, Any] | None) -> str | None:
    """The Firebase installation id the backend knows this client by."""
    try:
        return credentials["fcm"]["installation"]["fid"]  # type: ignore[index]
    except (KeyError, TypeError):
        return None


def registration_token(credentials: dict[str, Any] | None) -> str | None:
    try:
        return credentials["fcm"]["registration"]["token"]  # type: ignore[index]
    except (KeyError, TypeError):
        return None


class FermaxPushListener:
    """Owns the FCM connection and the backend token registration."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: FermaxDuoxmeClient,
        on_push: Callable[[dict[str, Any]], None],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._client = client
        self._on_push = on_push
        self._persistent_ids: list[str] = list(
            entry.data.get(CONF_FCM_PERSISTENT_IDS, [])
        )
        self._push: FcmPushClient | None = None
        self.app_token: str | None = None

    async def async_start(self) -> None:
        """Register with FCM and the backend, then start listening.

        Raises on failure; the caller decides whether that is fatal.
        """
        self._push = FcmPushClient(
            self._handle_message,
            FcmRegisterConfig(
                project_id=FCM_PROJECT_ID,
                app_id=FCM_APP_ID,
                api_key=FCM_API_KEY,
                messaging_sender_id=FCM_SENDER_ID,
            ),
            credentials=self._entry.data.get(CONF_FCM_CREDENTIALS),
            credentials_updated_callback=self._save_credentials,
            received_persistent_ids=self._persistent_ids,
            http_client_session=async_get_clientsession(self._hass),
        )
        token = await self._push.checkin_or_register()
        fid = installation_id(self._push.credentials)
        if fid is None:
            raise RuntimeError("FCM registration returned no installation id")
        # Re-register on every start: it is idempotent (force=True) and keeps
        # the backend's copy current if FCM rotated the token.
        await self._client.async_register_app_token(
            fid, token, locale=self._hass.config.language
        )
        self.app_token = token
        await self._push.start()
        _LOGGER.debug("Listening for DuoxMe push notifications")

    async def async_stop(self) -> None:
        if self._push is not None and self._push.is_started():
            await self._push.stop()

    @callback
    def _save_credentials(self, credentials: dict[str, Any]) -> None:
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, CONF_FCM_CREDENTIALS: credentials},
        )

    @callback
    def _handle_message(
        self, message: dict[str, Any], persistent_id: str, _context: Any
    ) -> None:
        # Runs inside the push client's task on the event loop.
        if persistent_id:
            self._persistent_ids.append(persistent_id)
            del self._persistent_ids[:-_MAX_PERSISTENT_IDS]
            self._hass.config_entries.async_update_entry(
                self._entry,
                data={
                    **self._entry.data,
                    CONF_FCM_PERSISTENT_IDS: list(self._persistent_ids),
                },
            )
        data = message.get("data")
        payload = data if isinstance(data, dict) else message
        _LOGGER.debug("Push received: %s", payload)
        self._on_push(payload)
