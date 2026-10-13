# Fermax DuoxMe — Home Assistant integration

Custom integration for the **Fermax DuoxMe** (a.k.a. Fermax Blue) video door
entry system. It talks to the same cloud API the official Android app uses, so
it works without any extra hardware.

> Reverse-engineered from `com.fermax.blue.app` 4.4.1. Unofficial — not
> affiliated with or endorsed by Fermax. Use with your own account.

## What it exposes

| Platform | Entity | Notes |
|----------|--------|-------|
| `lock` | One per visible door | `unlock` opens the door, then auto-relocks after a few seconds (the relay is momentary). Great for voice/HomeKit. |
| `button` | `Open <door>` per visible door | Clean momentary "open" action. |
| `switch` | `Auto-on` per device | Auto-answer calls. *Best-effort* (disabled by default). |
| `sensor` | Connection state, Last call, Wi-Fi signal | Last call comes from the call registry. |
| `binary_sensor` | Connectivity | Device online/offline. |

### Why no live video / answering calls?

The intercom's audio/video uses a WebRTC signalling server
(`signaling-pro-duoxme.fermax.io`) and a full media stack that isn't practical
to run inside Home Assistant. Opening doors, status and the call registry are all
done over the plain REST API and are fully supported here. You can still build
automations like *"when the last-call sensor updates, send me a notification"*.

## Install

### HACS (custom repository)
1. HACS → Integrations → ⋮ → *Custom repositories*.
2. Add `https://github.com/PedroGrilo/ha-fermax-duox-me`, category **Integration**.
3. Install **Fermax DuoxMe**, then restart Home Assistant.

### Manual
Copy `custom_components/fermax_duoxme/` into your Home Assistant
`config/custom_components/` directory and restart.

## Configure
Settings → Devices & Services → **Add Integration** → *Fermax DuoxMe*, then sign
in with your DuoxMe app email + password. Tokens are stored and refreshed
automatically.

Options (⚙️) let you change the polling interval (default 60 s, min 15 s).

## Test the API without Home Assistant
```bash
pip install aiohttp
python3 tools/test_api.py you@example.com 'your-password'
```
This logs in, lists your devices and their doors, and (optionally) opens one.

## Verified vs. best-effort endpoints
- **Verified from the app:** OAuth login/refresh, `pairings/me`, `users/me`,
  `directed-opendoor`.
- **Best-effort** (recovered from the app but request/response shape not
  confirmed against a live account): `auto-on`, do-not-disturb/mute, call
  registry, photocaller, extended device info. These are guarded so they never
  break the rest of the integration. If one misbehaves on your account, open an
  issue with the debug log and we can fix the payload.

## Debug logging
```yaml
logger:
  logs:
    custom_components.fermax_duoxme: debug
```
