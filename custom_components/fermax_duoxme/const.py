"""Constants for the Fermax DuoxMe integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "fermax_duoxme"

# --- Cloud API (reverse-engineered from com.fermax.blue.app 4.4.1) ---
# Production DuoxMe backend (replaces the older blue.fermax.com host).
OAUTH_URL = "https://oauth-pro-duoxme.fermax.io/oauth/token"
API_BASE = "https://pro-duoxme.fermax.io"

# OAuth2 "Resource Owner Password Credentials" client shipped in the app.
# Basic base64("duoxme:qxhx6zha5mjcypz42k2h8owhnkxbt_mixcs.zc9dp5ngo2djzfu7ignqe9abbm54yu")
OAUTH_BASIC_AUTH = (
    "Basic ZHVveG1lOnF4aHg2emhhNW1qY3lwejQyazJoOG93aG5reGJ0X21peGNz"
    "LnpjOWRwNW5nbzJkanpmdTdpZ25xZTlhYmJtNTR5dQ=="
)

USER_AGENT = "okhttp/4.12.0"

# --- Config entry keys ---
CONF_EMAIL = "email"
CONF_PASSWORD = "password"  # noqa: S105 - key name, not a secret
CONF_ACCESS_TOKEN = "access_token"  # noqa: S105
CONF_REFRESH_TOKEN = "refresh_token"  # noqa: S105
CONF_TOKEN_EXPIRES_AT = "token_expires_at"  # noqa: S105
CONF_UPDATE_INTERVAL = "update_interval"

# --- Behaviour ---
DEFAULT_UPDATE_INTERVAL = 60  # seconds
MIN_UPDATE_INTERVAL = 15
# How long a lock entity shows "unlocked" after an open-door pulse before it
# relocks itself (the relay is momentary; there is no real lock state to read).
LOCK_RELOCK_DELAY = timedelta(seconds=5)

# Door map keys as used by the backend (accessDoorMap).
DOOR_KEYS = ("ZERO", "ONE", "TWO", "THREE")

# Attribution shown on entities.
ATTRIBUTION = "Data provided by Fermax DuoxMe cloud"
