"""Async client for the Fermax DuoxMe (Blue) cloud API.

Reverse-engineered from the official Android app (com.fermax.blue.app 4.4.1).
Only the parts that make sense for a home-automation integration are exposed:
authentication, listing pairings/devices, opening doors, device feature toggles,
and the call registry. Live video / WebRTC signalling is intentionally omitted.

Endpoints marked "best-effort" below were recovered from the app's string table
but the exact request/response shape could not be verified against a live
account; they are guarded so a failure never breaks the rest of the integration.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from json import loads as json_loads
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    API_BASE,
    APP_BUILD,
    APP_VERSION,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_URL,
    USER_AGENT,
)


def _basic_auth_header() -> str:
    """HTTP Basic header the app builds from its OAuth client credentials."""
    raw = f"{OAUTH_CLIENT_ID}:{OAUTH_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


_LOGGER = logging.getLogger(__name__)

# Refresh the access token this many seconds before it actually expires.
_TOKEN_EXPIRY_MARGIN = 60

_REQUEST_TIMEOUT = ClientTimeout(total=20)


class FermaxError(Exception):
    """Base error for the Fermax DuoxMe client."""


class FermaxAuthError(FermaxError):
    """Authentication failed (bad credentials or expired/invalid refresh token)."""


class FermaxApiError(FermaxError):
    """A non-auth API call failed."""


@dataclass
class FermaxTokens:
    """OAuth tokens plus absolute expiry (epoch seconds)."""

    access_token: str
    refresh_token: str
    expires_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        return time.time() >= (self.expires_at - _TOKEN_EXPIRY_MARGIN)


_DOOR_DEFAULT_NAMES = {
    "ZERO": "Door",
    "ONE": "Door 1",
    "TWO": "Door 2",
    "THREE": "Door 3",
    "GENERAL": "General door",
}


@dataclass
class AccessDoor:
    """One openable door within a device's accessDoorMap."""

    key: str  # ZERO / ONE / TWO / THREE / GENERAL
    title: str
    visible: bool
    access_id: dict[str, int]  # {"block": .., "subblock": .., "number": ..}

    @property
    def display_name(self) -> str:
        """Human name: the backend title, or a friendly default when it's blank."""
        if self.title and self.title.strip():
            return self.title.strip()
        return _DOOR_DEFAULT_NAMES.get(self.key.upper(), self.key.title())


@dataclass
class FermaxDevice:
    """A paired DuoxMe device (monitor/panel) and its openable doors."""

    pairing_id: str
    device_id: str
    name: str
    is_master: bool
    connection_state: Any
    raw: dict[str, Any] = field(default_factory=dict)
    doors: list[AccessDoor] = field(default_factory=list)

    @property
    def available(self) -> bool:
        """Best-effort connectivity read from the pairing payload."""
        state = self.connection_state
        if isinstance(state, bool):
            return state
        if isinstance(state, (int, float)):
            return state != 0
        if isinstance(state, str):
            return state.upper() in ("CONNECTED", "ONLINE", "TRUE", "1")
        return True  # unknown -> assume reachable


def _parse_device(pairing: dict[str, Any]) -> FermaxDevice:
    """Turn one pairings/me entry into a FermaxDevice."""
    device_id = str(pairing.get("deviceId") or pairing.get("device_id") or "")
    name = (
        pairing.get("tag")
        or pairing.get("home")
        or pairing.get("name")
        or f"DuoxMe {device_id[-6:]}"
    )

    doors: list[AccessDoor] = []
    access_map = pairing.get("accessDoorMap") or {}
    if isinstance(access_map, dict):
        for key, entry in access_map.items():
            if not isinstance(entry, dict):
                continue
            access_id = entry.get("accessId") or entry.get("access_id") or {}
            if not isinstance(access_id, dict):
                continue
            doors.append(
                AccessDoor(
                    key=str(key),
                    title=str(entry.get("title") or ""),
                    visible=bool(entry.get("visible", True)),
                    access_id={
                        "block": int(access_id.get("block") or 0),
                        "subblock": int(access_id.get("subblock") or 0),
                        "number": int(access_id.get("number") or 0),
                    },
                )
            )

    return FermaxDevice(
        pairing_id=str(pairing.get("id") or pairing.get("pairingId") or device_id),
        device_id=device_id,
        name=str(name),
        is_master=bool(pairing.get("master", False)),
        connection_state=pairing.get("connectionState"),
        raw=pairing,
        doors=doors,
    )


class FermaxDuoxmeClient:
    """Thin async wrapper around the DuoxMe cloud API."""

    def __init__(
        self,
        session: ClientSession,
        tokens: FermaxTokens | None = None,
        on_tokens_refreshed: Callable[[FermaxTokens], None] | None = None,
    ) -> None:
        self._session = session
        self._tokens = tokens
        self._on_tokens_refreshed = on_tokens_refreshed
        # Serialises refreshes so concurrent callers don't burn the refresh token.
        self._refresh_lock = asyncio.Lock()

    # ------------------------------------------------------------------ auth

    @property
    def tokens(self) -> FermaxTokens | None:
        return self._tokens

    async def async_login(self, email: str, password: str) -> FermaxTokens:
        """Log in with email/password (OAuth2 password grant)."""
        self._tokens = await self._token_request(
            {
                "grant_type": "password",
                "username": email,
                "password": password,
            }
        )
        return self._tokens

    async def async_refresh(self) -> FermaxTokens:
        """Refresh the access token using the stored refresh token."""
        if not self._tokens:
            raise FermaxAuthError("No tokens available to refresh")
        stale_token = self._tokens.access_token
        async with self._refresh_lock:
            if self._tokens.access_token != stale_token:
                # Another caller refreshed while we were waiting for the lock.
                return self._tokens
            self._tokens = await self._token_request(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._tokens.refresh_token,
                }
            )
            if self._on_tokens_refreshed is not None:
                self._on_tokens_refreshed(self._tokens)
        return self._tokens

    async def _token_request(self, data: dict[str, str]) -> FermaxTokens:
        # Exactly as the app: Basic client auth in the header, grant fields in an
        # x-www-form-urlencoded body.
        headers = {
            "Authorization": _basic_auth_header(),
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with self._session.post(
                OAUTH_URL, data=data, headers=headers, timeout=_REQUEST_TIMEOUT
            ) as resp:
                if resp.status in (400, 401, 403):
                    text = await resp.text()
                    raise FermaxAuthError(f"OAuth rejected ({resp.status}): {text}")
                resp.raise_for_status()
                body = await resp.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as err:
            raise FermaxApiError(f"OAuth request failed: {err}") from err

        if not isinstance(body, dict):
            raise FermaxApiError(f"Unexpected OAuth response: {body!r}")
        access = body.get("access_token")
        refresh = body.get("refresh_token")
        if not access or not refresh:
            raise FermaxAuthError(
                f"OAuth response missing tokens (keys: {sorted(body)})"
            )
        expires_in = float(body.get("expires_in", 3600))
        return FermaxTokens(
            access_token=access,
            refresh_token=refresh,
            expires_at=time.time() + expires_in,
        )

    async def _async_valid_token(self) -> str:
        if not self._tokens:
            raise FermaxAuthError("Not authenticated")
        if self._tokens.is_expired:
            await self.async_refresh()
        return self._tokens.access_token

    # --------------------------------------------------------------- request

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> Any:
        token = await self._async_valid_token()
        url = f"{API_BASE}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        try:
            async with self._session.request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                status = resp.status
                text = await resp.text()
        except (ClientError, TimeoutError) as err:
            raise FermaxApiError(f"{method} {path} error: {err}") from err

        if status == 401:
            if not retry_auth:
                raise FermaxAuthError("Unauthorized after refresh")
            # Token may have been revoked server-side; refresh once.
            await self.async_refresh()
            return await self._request(
                method, path, json=json, params=params, retry_auth=False
            )
        if status >= 400:
            raise FermaxApiError(f"{method} {path} -> {status}: {text}")
        if not text:
            return None
        try:
            return json_loads(text)
        except ValueError:  # some endpoints return plain text
            return text

    # ----------------------------------------------------------- high level

    async def async_get_user(self) -> dict[str, Any]:
        """GET /user/api/v1/users/me — account profile."""
        return await self._request("GET", "user/api/v1/users/me") or {}

    async def async_get_devices(self) -> list[FermaxDevice]:
        """GET /pairing/api/v4/pairings/me — paired homes/devices with their doors."""
        data = await self._request("GET", "pairing/api/v4/pairings/me")
        pairings: list[dict[str, Any]]
        if isinstance(data, list):
            pairings = data
        elif isinstance(data, dict):
            # Some tenants wrap the list; accept a few shapes.
            pairings = (
                data.get("pairings")
                or data.get("content")
                or data.get("data")
                or []
            )
        else:
            pairings = []
        return [_parse_device(p) for p in pairings if isinstance(p, dict)]

    async def async_open_door(self, device_id: str, access_id: dict[str, int]) -> None:
        """POST /deviceaction/api/v1/device/{deviceId}/directed-opendoor.

        Body is the door's accessId: {"block", "number", "subblock"}
        (DirectOpenDoorParamsRequest).
        """
        await self._request(
            "POST",
            f"deviceaction/api/v1/device/{device_id}/directed-opendoor",
            json={
                "block": int(access_id.get("block", 0)),
                "number": int(access_id.get("number", 0)),
                "subblock": int(access_id.get("subblock", 0)),
            },
        )

    async def async_get_device_info(self, device_id: str) -> dict[str, Any]:
        """GET /deviceaction/api/v1/device/{id} — Monitor info.

        Returns fields like connectionState, wirelessSignal, subtype,
        blueStream, photocaller.
        """
        data = await self._request("GET", f"deviceaction/api/v1/device/{device_id}")
        return data if isinstance(data, dict) else {}

    # ----------------------------------------------------- best-effort extras

    async def async_trigger_auto_on(self, device_id: str) -> Any:
        """POST /deviceaction/api/v1/device/{id}/autoon — trigger auto-on.

        Best-effort: the v1 endpoint takes an optional ``directedTo`` query and
        no body. It's a momentary action (there is no documented "off"), so it's
        exposed as a button rather than a switch.
        """
        return await self._request(
            "POST",
            f"deviceaction/api/v1/device/{device_id}/autoon",
        )

    async def async_register_app_token(
        self, installation_id: str, token: str, *, locale: str = "en"
    ) -> None:
        """POST /notification/api/v2/apptoken — subscribe a push token.

        Mirrors RegisterPushTokenUseCase: ``id`` is the Firebase installation id,
        ``token`` the FCM registration token. The backend caps how many mobile
        tokens an account may have (maxMobileTokens in the error body).
        """
        await self._request(
            "POST",
            "notification/api/v2/apptoken",
            json=self._app_token_body(installation_id, token, locale, active=True),
        )

    async def async_unregister_app_token(
        self, installation_id: str, token: str, *, locale: str = "en"
    ) -> None:
        """DELETE /notification/api/v2/apptoken/{id} — drop a push token."""
        await self._request(
            "DELETE",
            f"notification/api/v2/apptoken/{installation_id}",
            json=self._app_token_body(installation_id, token, locale, active=False),
        )

    @staticmethod
    def _app_token_body(
        installation_id: str, token: str, locale: str, *, active: bool
    ) -> dict[str, Any]:
        return {
            "id": installation_id,
            "token": token,
            "appVersion": APP_VERSION,
            "locale": locale,
            "os": "ANDROID",
            "osVersion": "14",
            "appBuild": APP_BUILD,
            "phoneMobile": "Home Assistant",
            "active": active,
            "force": active,
        }

    async def async_get_call_registry(
        self, app_token: str, *, registry_type: str = "all"
    ) -> list[dict[str, Any]]:
        """GET /callmanager/api/v1/callregistry/participant — recent calls.

        The registry is per push token (``appToken``), not per device; entries
        carry ``deviceId``, ``callDate`` and, with photocaller, a ``photoId``.
        """
        data = await self._request(
            "GET",
            "callmanager/api/v1/callregistry/participant",
            params={"appToken": app_token, "callRegistryType": registry_type},
        )
        if isinstance(data, dict):
            data = data.get("content") or data.get("data") or []
        if not isinstance(data, list):
            return []
        return [call for call in data if isinstance(call, dict)]

    async def async_get_call_photo(self, photo_id: str) -> bytes | None:
        """GET /callmanager/api/v1/photocall?photoId= — photocaller snapshot."""
        data = await self._request(
            "GET", "callmanager/api/v1/photocall", params={"photoId": photo_id}
        )
        if not isinstance(data, dict):
            return None
        encoded = (data.get("base64Image") or {}).get("data")
        if not encoded:
            return None
        # Tolerate a data-URI prefix ("data:image/jpeg;base64,...").
        encoded = encoded.split(",", 1)[-1]
        try:
            return base64.b64decode(encoded)
        except (binascii.Error, ValueError) as err:
            raise FermaxApiError(f"Invalid photo data for {photo_id}: {err}") from err

    async def async_set_photocaller(self, device_id: str, enabled: bool) -> Any:
        """PUT /deviceaction/api/v1/{deviceId}/photocaller?value=<bool> (best-effort)."""
        return await self._request(
            "PUT",
            f"deviceaction/api/v1/{device_id}/photocaller",
            params={"value": str(enabled).lower()},
        )
