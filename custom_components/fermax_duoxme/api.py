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

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientResponseError, ClientSession, FormData

from .const import (
    API_BASE,
    OAUTH_BASIC_AUTH,
    OAUTH_URL,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# Refresh the access token this many seconds before it actually expires.
_TOKEN_EXPIRY_MARGIN = 60


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


@dataclass
class AccessDoor:
    """One openable door within a device's accessDoorMap."""

    key: str  # ZERO / ONE / TWO / THREE
    title: str
    visible: bool
    access_id: dict[str, int]  # {"block": .., "subblock": .., "number": ..}


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
                    title=str(entry.get("title") or key.title()),
                    visible=bool(entry.get("visible", True)),
                    access_id={
                        "block": int(access_id.get("block", 0)),
                        "subblock": int(access_id.get("subblock", 0)),
                        "number": int(access_id.get("number", 0)),
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

    def __init__(self, session: ClientSession, tokens: FermaxTokens | None = None) -> None:
        self._session = session
        self._tokens = tokens

    # ------------------------------------------------------------------ auth

    @property
    def tokens(self) -> FermaxTokens | None:
        return self._tokens

    async def async_login(self, email: str, password: str) -> FermaxTokens:
        """Log in with email/password (OAuth2 password grant)."""
        data = FormData()
        data.add_field("grant_type", "password")
        data.add_field("username", email)
        data.add_field("password", password)
        self._tokens = await self._token_request(data)
        return self._tokens

    async def async_refresh(self) -> FermaxTokens:
        """Refresh the access token using the stored refresh token."""
        if not self._tokens:
            raise FermaxAuthError("No tokens available to refresh")
        data = FormData()
        data.add_field("grant_type", "refresh_token")
        data.add_field("refresh_token", self._tokens.refresh_token)
        self._tokens = await self._token_request(data)
        return self._tokens

    async def _token_request(self, data: FormData) -> FermaxTokens:
        headers = {
            "Authorization": OAUTH_BASIC_AUTH,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        try:
            async with self._session.post(
                OAUTH_URL, data=data, headers=headers
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status in (400, 401, 403):
                    raise FermaxAuthError(f"OAuth rejected ({resp.status}): {body}")
                resp.raise_for_status()
        except FermaxAuthError:
            raise
        except ClientResponseError as err:
            raise FermaxApiError(f"OAuth request failed: {err}") from err
        except Exception as err:  # noqa: BLE001 - network/JSON errors
            raise FermaxApiError(f"OAuth request error: {err}") from err

        access = body.get("access_token")
        refresh = body.get("refresh_token")
        if not access or not refresh:
            raise FermaxAuthError(f"OAuth response missing tokens: {body}")
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
                method, url, json=json, params=params, headers=headers
            ) as resp:
                if resp.status == 401 and retry_auth:
                    # Token may have been revoked server-side; refresh once.
                    await self.async_refresh()
                    return await self._request(
                        method, path, json=json, params=params, retry_auth=False
                    )
                if resp.status == 401:
                    raise FermaxAuthError("Unauthorized after refresh")
                text = await resp.text()
                if resp.status >= 400:
                    raise FermaxApiError(f"{method} {path} -> {resp.status}: {text}")
                if not text:
                    return None
                try:
                    return await resp.json(content_type=None)
                except Exception:  # noqa: BLE001 - some endpoints return plain text
                    return text
        except (FermaxAuthError, FermaxApiError):
            raise
        except Exception as err:  # noqa: BLE001
            raise FermaxApiError(f"{method} {path} error: {err}") from err

    # ----------------------------------------------------------- high level

    async def async_get_user(self) -> dict[str, Any]:
        """GET api/v1/users/me — account profile."""
        return await self._request("GET", "api/v1/users/me") or {}

    async def async_get_devices(self) -> list[FermaxDevice]:
        """GET api/v4/pairings/me — paired homes/devices with their doors."""
        data = await self._request("GET", "api/v4/pairings/me")
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
        """POST api/v1/device/{deviceId}/directed-opendoor — open a specific door.

        Body is the door's accessId: {"block", "subblock", "number"}.
        """
        await self._request(
            "POST",
            f"api/v1/device/{device_id}/directed-opendoor",
            json={
                "block": int(access_id.get("block", 0)),
                "subblock": int(access_id.get("subblock", 0)),
                "number": int(access_id.get("number", 0)),
            },
        )

    # ----------------------------------------------------- best-effort extras

    async def async_set_auto_on(self, device_id: str, enabled: bool) -> Any:
        """POST api/v2/device/{id}/autoon — toggle auto-on (best-effort body)."""
        return await self._request(
            "POST",
            f"api/v2/device/{device_id}/autoon",
            json={"autoOn": enabled},
        )

    async def async_get_device_info(self, device_id: str) -> dict[str, Any]:
        """GET api/v1/device/{id} — extended device info (firmware, wifi, ...)."""
        data = await self._request("GET", f"api/v1/device/{device_id}")
        return data if isinstance(data, dict) else {}

    async def async_set_do_not_disturb(self, enabled: bool) -> Any:
        """POST api/v1/mutedevice/me — mute/do-not-disturb for this account.

        Best-effort: field name inferred from the app (doNotDisturb).
        """
        return await self._request(
            "POST",
            "api/v1/mutedevice/me",
            json={"doNotDisturb": enabled},
        )

    async def async_get_call_registry(
        self, device_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """GET api/v1/callregistry/participants — recent calls (best-effort)."""
        try:
            data = await self._request(
                "GET",
                "api/v1/callregistry/participants",
                params={"deviceId": device_id, "size": limit},
            )
        except FermaxApiError as err:
            _LOGGER.debug("call registry unavailable for %s: %s", device_id, err)
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("content") or data.get("data") or []
        return []

    async def async_get_photocaller(self, device_id: str) -> list[dict[str, Any]]:
        """GET api/v1/{deviceId}/photocaller — recent caller snapshots (best-effort)."""
        try:
            data = await self._request("GET", f"api/v1/{device_id}/photocaller")
        except FermaxApiError as err:
            _LOGGER.debug("photocaller unavailable for %s: %s", device_id, err)
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("content") or data.get("data") or []
        return []
