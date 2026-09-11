<p align="center">
  <img src="assets/logo.svg" width="128" alt="appletv-mcp logo"/>
</p>

<h1 align="center">appletv-mcp</h1>

<p align="center">
  <strong>The complete MCP server for agentic Apple TV control.</strong><br/>
  Let Claude, Cursor, opencode, or any MCP client find, play, and control anything on your Apple TV — by name, by URL, or by button press.
</p>

<p align="center">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue"/>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab"/>
  <img alt="tvOS 26 tested" src="https://img.shields.io/badge/tvOS-26%20tested-000000"/>
  <img alt="MCP compatible" src="https://img.shields.io/badge/MCP-compatible-5e5ce6"/>
  <a href="https://m8ven.ai/mcp/slandau3/appletv-mcp"><img alt="M8ven Score" src="https://m8ven.ai/badge/mcp/slandau3/appletv-mcp"/></a>
</p>

---

**"Watch Severance."** — resolves the title via JustWatch, deep-links straight into the episode on Apple TV+.

**"Put the new Veritasium video on the TV."** — finds it on YouTube and plays it full-screen via AirPlay.

**"Turn the volume down and pause."** — done.

No remote. No Siri. No cloud. Everything runs on your local network over Apple's own protocols, powered by [pyatv](https://github.com/postlund/pyatv).

> [!TIP]
> *"Please play Severance season 2 episode 1 on the TV."*

## Why this one

| | appletv-mcp | mcp-pyatv | mcp-tv | Home Assistant |
|---|---|---|---|---|
| "Watch \<title\>" by name (JustWatch → deep link) | ✅ | ❌ | ❌ | manual URLs |
| Play **any** video URL / ~1,800 sites (yt-dlp → AirPlay) | ✅ | ❌ | ❌ | ❌ |
| YouTube by search terms | ✅ | ❌ | ❌ | ❌ |
| Deep links into titles (Apple TV+, Disney+, Max, Hulu, …) | ✅ | ❌ | ❌ | manual |
| App deep-link catalog in docs | ✅ | ❌ | ❌ | partial |
| Absolute volume 0–100, seek, repeat, shuffle | ✅ | ❌ | ❌ | partial |
| AirPlay audio output routing (HomePods) | ✅ | ❌ | ❌ | ❌ |
| Multi-TV with per-tool targeting + self-healing IPs | ✅ | ❌ | ❌ | partial |
| tvOS 26 AirPlay video push that actually works | ✅ ([fork pin](#notes)) | ❌ | ❌ | ❌ |
| No always-on automation stack required | ✅ | ✅ | ✅ | ❌ |
| Companion skill for non-MCP agents | ✅ [appletv-remote](https://github.com/slandau3/appletv-remote) | ❌ | ❌ | ❌ |

## Quickstart

**Prerequisite:** [uv](https://docs.astral.sh/uv/) (`brew install uv`).

**1. Pair** (once per TV; the TV shows a 4-digit PIN twice):

```bash
git clone https://github.com/slandau3/appletv-mcp.git && cd appletv-mcp
uv run python scripts/pair.py
```

Credentials live owner-only (0600) in `~/.config/appletv-remote/devices.json`,
shared with the [appletv-remote](https://github.com/slandau3/appletv-remote)
skill. Keep that file private — it grants full control of the TV.

**2. Register with your MCP client:**

Claude Code:

```bash
claude mcp add appletv -- uv run --directory /path/to/appletv-mcp python server.py
```

opencode (`~/.config/opencode/opencode.json`):

```json
{
  "mcp": {
    "appletv": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/path/to/appletv-mcp", "python", "server.py"],
      "enabled": true
    }
  }
}
```

## Tools (18)

**Content discovery & playback**

| Tool | What it does |
| --- | --- |
| `atv_watch` | Find a movie/show by name (JustWatch) and deep-link straight into it. Optional `service` ("netflix", "disney+", "apple tv", "max", "hulu", "prime") |
| `atv_play` | Play ANY video URL in the native system player: direct media (.mp4/.m3u8/...) or ~1,800 sites via yt-dlp (Vimeo, Dailymotion, Twitch, X, news embeds). No DRM services |
| `atv_youtube` | Play a YouTube video by URL, ID, or plain search terms. `app=True` opens in the YouTube app instead |
| `atv_open_url` | Open any deep link; tvOS routes it to the right app |

**Remote & navigation**

| Tool | What it does |
| --- | --- |
| `atv_remote` | 26 buttons: arrows, select, menu, home, top_menu, home_hold, control_center, guide, screensaver, play/pause/play_pause/stop/next/previous, skip_forward/backward, channel up/down, volume up/down, suspend, wakeup |
| `atv_type` | Type into the focused on-screen field (optional `clear` first) |
| `atv_seek` | Seek to a position in seconds |

**Audio**

| Tool | What it does |
| --- | --- |
| `atv_volume` | Get volume, step up/down, or set absolute 0–100 |
| `atv_audio_outputs` | List AirPlay audio outputs (HomePods) or route audio to one |
| `atv_repeat` / `atv_shuffle` | Repeat (off/track/all) and shuffle (off/songs/albums) |

**Apps & system**

| Tool | What it does |
| --- | --- |
| `atv_launch_app` / `atv_list_apps` | Launch by friendly name (netflix, disney+, max, hulu, prime video, peacock, paramount+, plex, spotify, …) or bundle id; list installed apps |
| `atv_now_playing` | Title, artist, frontmost app, position, playback state |
| `atv_power` | Sleep/wake (HDMI-CEC turns the TV off too) |
| `atv_devices` / `atv_use` / `atv_scan` | Multi-TV management: list paired TVs, set the default, scan the network |

Control tools accept an optional `device` name to target a non-default TV.

## How it works

pyatv speaks Apple's **Companion** protocol (remote buttons, apps,
keyboard, deep links, text input) and **AirPlay 2** (video push, volume,
output routing). Pairing happens once via on-screen PIN; after that,
every tool call is a local, encrypted, no-cloud interaction. When a TV's
IP changes, the server re-finds it by its stable identifier and updates
the config itself.

## Notes

- **pyatv is pinned to an exact commit of an unmerged fork** fixing
  `play_url` on tvOS 26 ([PR #2846](https://github.com/postlund/pyatv/pull/2846),
  issues [#2821](https://github.com/postlund/pyatv/issues/2821)/[#2774](https://github.com/postlund/pyatv/issues/2774)).
  Once merged and released, we'll switch back to stock pyatv.
- **Netflix deep links are unreliable** since its Sept 2025 tvOS app
  update; `atv_watch` reports availability and you can fall back to
  `atv_launch_app("netflix")` + `atv_type(...)`.
- Some sites (e.g. Vimeo) bot-block yt-dlp extraction with a 403 —
  AirPlay from a phone/browser is the fallback.
- JustWatch lookups default to US/en; override with `ATV_JW_COUNTRY` /
  `ATV_JW_LANGUAGE`.
- Streamed URLs are IP-bound and expire (~6h) — always resolve fresh.
- Tested on tvOS 26 (Apple TV 4K gen 3). tvOS 13+ should work.

## Contributing

Issues and PRs welcome. The codebase is one readable file
(`server.py`, ~700 lines) plus a pairing script — easy to hack on.

## License

MIT — see [LICENSE](LICENSE).
