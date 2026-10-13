#!/usr/bin/env python3
"""Standalone smoke test for the Fermax DuoxMe cloud API.

Does NOT need Home Assistant — only aiohttp. Logs in, lists devices/doors,
and optionally opens a door so you can confirm the reverse-engineered API
works with your account before wiring it into HA.

Usage:
    pip install aiohttp
    python3 tools/test_api.py EMAIL PASSWORD
    python3 tools/test_api.py EMAIL PASSWORD --open DEVICE_ID DOOR_KEY

DOOR_KEY is one of ZERO/ONE/TWO/THREE (see the listing output).

Doorbell push + photos (registers this script as one more "phone"):
    pip install aiohttp firebase-messaging
    python3 tools/test_api.py EMAIL PASSWORD --listen
    python3 tools/test_api.py EMAIL PASSWORD --unregister

--listen keeps its FCM identity in tools/.fcm_credentials.json, so re-runs
reuse the same push token instead of using up another phone slot. Ring the
doorbell; the push payload is printed and the call photo saved to tools/photos/.
--unregister removes that token from the account again.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

OAUTH_URL = "https://oauth-pro-duoxme.fermax.io/oauth/token"
API_BASE = "https://pro-duoxme.fermax.io"
# Real OAuth client from the app's BuildConfig (FLAVOR=BlueStreamPro, env=PRO).
CLIENT_ID = "oe87y4nj6a2vz3h63lrh8y1p8lp4zewrhymhwa6ngz5oxf0"
CLIENT_SECRET = "8r1e8jo3dk32o5i0i1l89wcgapp05sp8ossrpjnxrodv0wr"
BASIC = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
UA = "okhttp/4.12.0"

FCM_PROJECT_ID = "fermax-blue"
FCM_SENDER_ID = "879268374717"
FCM_APP_ID = "1:879268374717:android:b8e39b52f4a7452b926fd4"
# Firebase Web API key from the APK (public by design; identifies the project).
FCM_API_KEY = "AIzaSyAp-B-zKrSwkJqiXoC2K-WCywH8xZ7TKus"
TOOLS_DIR = Path(__file__).resolve().parent
CREDS_FILE = TOOLS_DIR / ".fcm_credentials.json"
PHOTOS_DIR = TOOLS_DIR / "photos"


async def login(session: aiohttp.ClientSession, email: str, password: str) -> str:
    async with session.post(
        OAUTH_URL,
        data={"grant_type": "password", "username": email, "password": password},
        headers={"Authorization": BASIC, "User-Agent": UA, "Accept": "application/json"},
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200:
            sys.exit(f"Login failed ({resp.status}): {body}")
        print(f"[ok] logged in; token expires in {body.get('expires_in')}s")
        return body["access_token"]


async def get(session: aiohttp.ClientSession, token: str, path: str):
    async with session.get(
        f"{API_BASE}/{path}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": UA},
    ) as resp:
        text = await resp.text()
        if resp.status >= 400:
            print(f"[warn] GET {path} -> {resp.status}: {text[:200]}")
            return None
        try:
            return await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            return text


async def open_door(session, token, device_id, access_id) -> None:
    async with session.post(
        f"{API_BASE}/deviceaction/api/v1/device/{device_id}/directed-opendoor",
        json=access_id,
        headers={"Authorization": f"Bearer {token}", "User-Agent": UA},
    ) as resp:
        print(f"[open] {device_id} {access_id} -> {resp.status} {await resp.text()}")


def app_token_body(fid: str, token: str, active: bool) -> dict:
    return {
        "id": fid, "token": token, "appVersion": "4.4.1", "locale": "en",
        "os": "ANDROID", "osVersion": "14", "appBuild": "759",
        "phoneMobile": "Home Assistant", "active": active, "force": active,
    }


async def send(session, token, method, path, *, json_body=None, params=None):
    async with session.request(
        method, f"{API_BASE}/{path}", json=json_body, params=params,
        headers={"Authorization": f"Bearer {token}", "User-Agent": UA},
    ) as resp:
        text = await resp.text()
        print(f"[{method}] {path} -> {resp.status} {text[:300]}")
        try:
            return resp.status, json.loads(text) if text else None
        except ValueError:
            return resp.status, text


async def fetch_latest_photo(session, token, app_token) -> None:
    _, calls = await send(
        session, token, "GET", "callmanager/api/v1/callregistry/participant",
        params={"appToken": app_token, "callRegistryType": "all"},
    )
    calls = calls if isinstance(calls, list) else []
    print(f"[calls] {len(calls)} entries; newest: {json.dumps(calls[:2], indent=1)}")
    with_photo = [c for c in calls if c.get("photoId")]
    if not with_photo:
        print("[photo] no call with a photoId yet")
        return
    newest = max(with_photo, key=lambda c: str(c.get("callDate") or ""))
    status, photo = await send(
        session, token, "GET", "callmanager/api/v1/photocall",
        params={"photoId": newest["photoId"]},
    )
    data = ((photo or {}).get("base64Image") or {}).get("data") if isinstance(photo, dict) else None
    if not data:
        print(f"[photo] no image in response ({status})")
        return
    PHOTOS_DIR.mkdir(exist_ok=True)
    out = PHOTOS_DIR / f"{newest['photoId']}.jpg"
    out.write_bytes(base64.b64decode(data.split(",", 1)[-1]))
    print(f"[photo] saved {out}")


async def listen(session, token, unregister: bool) -> None:
    from firebase_messaging import FcmPushClient, FcmRegisterConfig

    api_key = os.environ.get("FERMAX_FCM_API_KEY", FCM_API_KEY)
    creds = json.loads(CREDS_FILE.read_text()) if CREDS_FILE.exists() else None

    def save_creds(new_creds: dict) -> None:
        CREDS_FILE.write_text(json.dumps(new_creds))
        print(f"[fcm] credentials saved to {CREDS_FILE}")

    loop = asyncio.get_running_loop()
    rings: asyncio.Queue = asyncio.Queue()

    def on_push(message: dict, persistent_id: str, _ctx) -> None:
        print(f"\n[push] {persistent_id}: {json.dumps(message, indent=1)}")
        data = message.get("data") if isinstance(message.get("data"), dict) else message
        if data.get("FermaxNotificationType") == "Call":
            loop.call_soon_threadsafe(rings.put_nowait, data)

    push = FcmPushClient(
        on_push,
        FcmRegisterConfig(FCM_PROJECT_ID, FCM_APP_ID, api_key, FCM_SENDER_ID),
        credentials=creds,
        credentials_updated_callback=save_creds,
        http_client_session=session,
    )
    fcm_token = await push.checkin_or_register()
    fid = push.credentials["fcm"]["installation"]["fid"]
    print(f"[fcm] fid={fid} token={fcm_token[:24]}...")

    if unregister:
        await send(session, token, "DELETE", f"notification/api/v2/apptoken/{fid}",
                   json_body=app_token_body(fid, fcm_token, False))
        return

    status, _ = await send(session, token, "POST", "notification/api/v2/apptoken",
                           json_body=app_token_body(fid, fcm_token, True))
    if status >= 400:
        sys.exit("Token registration failed (maxMobileTokens = phone limit reached?)")

    await fetch_latest_photo(session, token, fcm_token)
    await push.start()
    print("\n[listen] waiting for the doorbell... (Ctrl-C to stop)")
    try:
        while True:
            ring = await rings.get()
            print(f"[ring] device {ring.get('DeviceId')} - fetching photo shortly")
            for delay in (5, 15, 40):
                await asyncio.sleep(delay)
                await fetch_latest_photo(session, token, fcm_token)
    finally:
        await push.stop()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("email")
    ap.add_argument("password")
    ap.add_argument("--open", nargs=2, metavar=("DEVICE_ID", "DOOR_KEY"))
    ap.add_argument("--listen", action="store_true",
                    help="register for push, wait for rings, save call photos")
    ap.add_argument("--unregister", action="store_true",
                    help="remove this script's push token from the account")
    args = ap.parse_args()

    async with aiohttp.ClientSession() as session:
        token = await login(session, args.email, args.password)

        if args.listen or args.unregister:
            await listen(session, token, args.unregister)
            return

        user = await get(session, token, "user/api/v1/users/me")
        print(f"[user] {user}")

        pairings = await get(session, token, "pairing/api/v4/pairings/me")
        if isinstance(pairings, dict):
            pairings = pairings.get("pairings") or pairings.get("content") or []
        pairings = pairings or []
        pairings = [p for p in pairings if isinstance(p, dict)]
        print(f"\n[devices] {len(pairings)} found:")
        for p in pairings:
            did = p.get("deviceId")
            print(f"  - {p.get('tag') or p.get('home')}  deviceId={did}  "
                  f"master={p.get('master')}  state={p.get('connectionState')}")
            for key, door in (p.get("accessDoorMap") or {}).items():
                print(f"      door {key}: title={door.get('title')!r} "
                      f"visible={door.get('visible')} accessId={door.get('accessId')}")

        if args.open:
            device_id, door_key = args.open
            match = next((p for p in pairings if p.get("deviceId") == device_id), None)
            if not match:
                sys.exit(f"device {device_id} not found")
            door = (match.get("accessDoorMap") or {}).get(door_key)
            if not door:
                sys.exit(f"door {door_key} not found on {device_id}")
            print(f"\nOpening {door.get('title')} in 2s... (Ctrl-C to abort)")
            time.sleep(2)
            await open_door(session, token, device_id, door["accessId"])


if __name__ == "__main__":
    asyncio.run(main())
