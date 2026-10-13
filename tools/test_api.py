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
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
import time

import aiohttp

OAUTH_URL = "https://oauth-pro-duoxme.fermax.io/oauth/token"
API_BASE = "https://pro-duoxme.fermax.io"
# Real OAuth client from the app's BuildConfig (FLAVOR=BlueStreamPro, env=PRO).
CLIENT_ID = "oe87y4nj6a2vz3h63lrh8y1p8lp4zewrhymhwa6ngz5oxf0"
CLIENT_SECRET = "8r1e8jo3dk32o5i0i1l89wcgapp05sp8ossrpjnxrodv0wr"
BASIC = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
UA = "okhttp/4.12.0"


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


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("email")
    ap.add_argument("password")
    ap.add_argument("--open", nargs=2, metavar=("DEVICE_ID", "DOOR_KEY"))
    args = ap.parse_args()

    async with aiohttp.ClientSession() as session:
        token = await login(session, args.email, args.password)

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
