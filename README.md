# appletv-mcp

An [MCP](https://modelcontextprotocol.io) server that gives an AI agent
full control of an Apple TV over the local network — no remote, no Siri.

Built on [pyatv](https://github.com/postlund/pyatv) (Companion + AirPlay
protocols). A sibling project,
[appletv-remote](https://github.com/slandau3/appletv-remote), provides
the same capabilities as a Claude Code / opencode skill instead of a
server.

## What it can do

| Tool | Capability |
| --- | --- |
| `atv_watch` | "Pull up Severance" — JustWatch search → deep link straight into the title (Apple TV+, Disney+, Max, Hulu, Prime, Peacock, …) |
| `atv_youtube` | Play any YouTube video by URL or plain search terms, full-screen via AirPlay (optionally inside the YouTube app) |
| `atv_open_url` | Open any deep link; tvOS routes it to the right app |
| `atv_launch_app` / `atv_list_apps` | Launch apps by friendly name; list installed apps |
| `atv_remote` | Full remote: navigation, select, menu, home, play/pause, stop, next/previous, volume, sleep/wake |
| `atv_type` | Type into focused on-screen fields (search boxes) |
| `atv_now_playing` | What's playing: title, state, position |
| `atv_power` | Sleep/wake, including HDMI-CEC TV power |
| `atv_devices` / `atv_scan` / `atv_use` | Manage paired TVs and the default; discover TVs on the network |

Control tools accept an optional `device` name to target a non-default
TV (`atv_scan`, `atv_devices`, and `atv_use` are the exceptions — they
apply to the pairing config itself).

## Setup

**Prerequisite:** [uv](https://docs.astral.sh/uv/) (`brew install uv`).

1. **Pair once per TV** (interactive; the TV shows a 4-digit PIN twice):

   ```bash
   uv run python scripts/pair.py
   ```

   Credentials are stored owner-only in
   `~/.config/appletv-remote/devices.json` (shared with the
   appletv-remote skill). Keep that file private — it grants full
   control of the TV.

   Non-interactive (agent-driven): set `ATV_PIN_FILE=/tmp/atv_pin.txt`,
   run `pair.py <name-filter>`, and write each PIN to that file.

2. **Register the server** with your MCP client. Example for opencode
   (`~/.config/opencode/opencode.json`):

   ```json
   "mcp": {
     "appletv": {
       "type": "local",
       "command": ["uv", "run", "--directory", "/path/to/appletv-mcp", "python", "server.py"],
       "enabled": true
     }
   }
   ```

   For Claude Code: `claude mcp add appletv -- uv run --directory /path/to/appletv-mcp python server.py`

## Notes and caveats

- **pyatv is pinned to an exact commit of an unmerged fork** fixing
  `play_url` on tvOS 26 ([PR #2846](https://github.com/postlund/pyatv/pull/2846),
  issues [#2821](https://github.com/postlund/pyatv/issues/2821)/[#2774](https://github.com/postlund/pyatv/issues/2774)).
  Once merged and released, switch back to stock `pyatv`.
- **Netflix deep links are unreliable** since its Sept 2025 tvOS app
  update; `atv_watch` falls back gracefully, and you can always
  `atv_launch_app("netflix")` + `atv_type(...)`.
- JustWatch lookups default to US/en; override with `ATV_JW_COUNTRY` /
  `ATV_JW_LANGUAGE`.
- YouTube streams are pushed as HLS via AirPlay 2: playback is queued on
  the TV and continues after the tool returns. URLs expire after ~6h.
- A `FetchAttentionState failed` warning at connect time is benign
  (fork quirk); commands still work.
- Tested on tvOS 26 (Apple TV 4K gen 3). tvOS 13+ should work.

## License

MIT
