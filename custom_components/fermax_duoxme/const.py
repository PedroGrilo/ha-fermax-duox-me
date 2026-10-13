"""Constants for the Fermax DuoxMe integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "fermax_duoxme"

# --- Cloud API (reverse-engineered from com.fermax.blue.app 4.4.1) ---
# Production DuoxMe backend (replaces the older blue.fermax.com host).
OAUTH_URL = "https://oauth-pro-duoxme.fermax.io/oauth/token"
API_BASE = "https://pro-duoxme.fermax.io"

# OAuth2 "Resource Owner Password Credentials" client, from the app's BuildConfig
# (FLAVOR=BlueStreamPro, environment=PRO). The app logs in with:
#   POST {oauth}/oauth/token
#   Authorization: Basic base64(client_id:client_secret)
#   body: username=<email>&password=<pw>&grant_type=password
# (Note: the duoxme:qxhx... value in the APK is TRACING_BASIC_AUTH for telemetry,
# NOT the OAuth client — using it returns invalid_client.)
OAUTH_CLIENT_ID = "oe87y4nj6a2vz3h63lrh8y1p8lp4zewrhymhwa6ngz5oxf0"
OAUTH_CLIENT_SECRET = "8r1e8jo3dk32o5i0i1l89wcgapp05sp8ossrpjnxrodv0wr"  # noqa: S105

USER_AGENT = "okhttp/4.12.0"

# Reported to the backend when registering for push notifications.
APP_VERSION = "4.4.1"
APP_BUILD = "759"

# Firebase project the app receives its push notifications from (the APK's
# google-services resources). Used to register Home Assistant as a push client
# so it gets doorbell calls in real time and can read the call registry.
FCM_PROJECT_ID = "fermax-blue"
FCM_SENDER_ID = "879268374717"
FCM_APP_ID = "1:879268374717:android:b8e39b52f4a7452b926fd4"
# The app's Firebase Web API key (google_api_key in resources.arsc). Public by
# design: it only identifies the Firebase project. Set it to "" to run without
# push (no ring events, call registry or photos).
FCM_API_KEY = "AIzaSyAp-B-zKrSwkJqiXoC2K-WCywH8xZ7TKus"

# Push payload keys (NotificationController in the app).
PUSH_TYPE = "FermaxNotificationType"
PUSH_DEVICE_ID = "DeviceId"
PUSH_ROOM_ID = "RoomId"
PUSH_ACCESS_DOOR = "AccessDoorKey"
PUSH_TYPE_CALL = "Call"

# --- Config entry keys ---
CONF_EMAIL = "email"
CONF_PASSWORD = "password"  # noqa: S105 - key name, not a secret
CONF_ACCESS_TOKEN = "access_token"  # noqa: S105
CONF_REFRESH_TOKEN = "refresh_token"  # noqa: S105
CONF_TOKEN_EXPIRES_AT = "token_expires_at"  # noqa: S105
CONF_UPDATE_INTERVAL = "update_interval"
CONF_FCM_CREDENTIALS = "fcm_credentials"
CONF_FCM_PERSISTENT_IDS = "fcm_persistent_ids"

# --- Behaviour ---
DEFAULT_UPDATE_INTERVAL = 60  # seconds
MIN_UPDATE_INTERVAL = 15
# How long a lock entity shows "unlocked" after an open-door pulse before it
# relocks itself (the relay is momentary; there is no real lock state to read).
LOCK_RELOCK_DELAY = timedelta(seconds=5)
# After a ring, the photo shows up in the call registry with some delay; poll
# the registry at these offsets (seconds) to pick it up quickly.
PHOTO_FETCH_DELAYS = (5, 15, 40)

# Attribution shown on entities.
ATTRIBUTION = "Data provided by Fermax DuoxMe cloud"
