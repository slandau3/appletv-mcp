"""appletv-mcp: control an Apple TV over the network via MCP.

Wraps pyatv (Companion + AirPlay protocols) in a FastMCP server so an
agent can navigate, launch apps, deep-link into titles, push YouTube
videos, and query playback state.

Pairing is a separate one-time step (scripts/pair.py); credentials are
shared with the appletv-remote skill in
~/.config/appletv-remote/devices.json.

NOTE: pyatv is pinned to an exact commit of an unmerged fork because
upstream play_url is broken on tvOS 26 (issues #2821/#2774, fix PR
#2846). Once that merges and ships, switch back to stock pyatv.
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx
import pyatv
from fastmcp import FastMCP
from pyatv.const import Protocol, RepeatState, ShuffleState

CONFIG_PATH = Path.home() / ".config" / "appletv-remote" / "devices.json"

APP_ALIASES = {
    "tv": "com.apple.TVWatchList",
    "apple tv": "com.apple.TVWatchList",
    "netflix": "com.netflix.Netflix",
    "youtube": "com.google.ios.youtube",
    "disney+": "com.disney.disneyplus",
    "disney": "com.disney.disneyplus",
    "max": "com.wbd.stream",
    "hbo": "com.wbd.stream",
    "hbo max": "com.wbd.stream",
    "hulu": "com.hulu.plus",
    "prime": "com.amazon.aiv.AIVApp",
    "prime video": "com.amazon.aiv.AIVApp",
    "amazon": "com.amazon.aiv.AIVApp",
    "peacock": "com.peacocktv.peacock",
    "paramount+": "com.cbsvideo.app",
    "paramount": "com.cbsvideo.app",
    "plex": "com.plexapp.plex",
    "spotify": "com.spotify.client",
    "music": "com.apple.TVMusic",
    "podcasts": "com.apple.podcasts",
    "photos": "com.apple.TVPhotos",
    "settings": "com.apple.TVSettings",
    "app store": "com.apple.TVAppStore",
    "arcade": "com.apple.Arcade",
    "fitness": "com.apple.Fitness",
    "facetime": "com.apple.facetime",
    "search": "com.apple.TVSearch",
}

REMOTE_ACTIONS = [
    "up", "down", "left", "right", "select", "menu", "home",
    "top_menu", "home_hold", "control_center", "guide", "screensaver",
    "play", "pause", "play_pause", "stop", "next", "previous",
    "skip_forward", "skip_backward", "channel_up", "channel_down",
    "volume_up", "volume_down", "suspend", "wakeup",
]

REPEAT_MODES = {
    "off": RepeatState.Off,
    "track": RepeatState.Track,
    "all": RepeatState.All,
}
SHUFFLE_MODES = {
    "off": ShuffleState.Off,
    "songs": ShuffleState.Songs,
    "albums": ShuffleState.Albums,
}

JUSTWATCH_GRAPHQL = "https://apis.justwatch.com/graphql"

# Vendors without a tvOS app to deep-link into (physical media etc.).
PROVIDER_BLOCKLIST = {
    "amazon dvd / blu-ray",
    "zavvi",
    "barnes & noble",
    "fye",
    "target",
    "walmart",
}

mcp = FastMCP("appletv")

_lock = asyncio.Lock()
_atv = None
_atv_name: str | None = None


# ---------------------------------------------------------------- config

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            f"No paired TVs (no {CONFIG_PATH}). Run scripts/pair.py first."
        )
    try:
        config = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Config at {CONFIG_PATH} is corrupt ({e}). "
            "Delete it and re-run scripts/pair.py."
        ) from e
    if not isinstance(config, dict):
        raise RuntimeError(
            f"Config at {CONFIG_PATH} has an unexpected shape. "
            "Delete it and re-run scripts/pair.py."
        )
    config.setdefault("default", None)
    config.setdefault("devices", {})
    return config


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_PATH.parent, 0o700)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(CONFIG_PATH, 0o600)  # credentials — owner read/write only


def get_device(config: dict, name: str | None) -> tuple[str, dict]:
    devices = config.get("devices", {})
    if not devices:
        raise RuntimeError("No paired TVs. Run scripts/pair.py first.")
    chosen = name or config.get("default")
    if chosen is None:
        raise RuntimeError(
            f"No default TV set. Paired: {', '.join(devices)}"
        )
    if chosen not in devices:
        raise RuntimeError(
            f"Unknown TV '{chosen}'. Paired: {', '.join(devices)}"
        )
    device = dict(devices[chosen])
    device["_name"] = chosen
    return chosen, device


# ------------------------------------------------------------ connection

async def find_conf(device: dict):
    """Locate the TV by IP, falling back to its stable identifier.

    Self-heals the stored address after a DHCP lease change."""
    loop = asyncio.get_running_loop()
    devices = await pyatv.scan(loop, timeout=5.0)
    conf = next(
        (c for c in devices if str(c.address) == device["address"]), None
    )
    if conf is None and device.get("identifier"):
        conf = next(
            (c for c in devices if c.identifier == device["identifier"]),
            None,
        )
        if conf is not None:
            config = load_config()
            entry = config["devices"].get(device.get("_name"))
            if entry is not None:
                entry["address"] = str(conf.address)
                save_config(config)
    if conf is None:
        raise RuntimeError(
            f"Apple TV at {device['address']} not found on the network. "
            "Is it awake and on the same network?"
        )
    return conf


async def _get_atv(device_name: str | None):
    """Return the cached connection, reconnecting when the target changes.

    The cache is keyed by resolved device name: asking for a different
    TV than the cached connection's closes it and reconnects."""
    global _atv, _atv_name
    async with _lock:
        config = load_config()
        resolved, device = get_device(config, device_name)
        if _atv is not None and _atv_name == resolved:
            return _atv
        if _atv is not None:
            try:
                _atv.close()
            except Exception:
                pass
            _atv = None
            _atv_name = None
        conf = await find_conf(device)
        companion = conf.get_service(Protocol.Companion)
        if companion is not None:
            companion.credentials = device["credentials"].get("companion")
        airplay = conf.get_service(Protocol.AirPlay)
        if airplay is not None:
            airplay.credentials = device["credentials"].get("airplay")
        _atv = await pyatv.connect(conf, asyncio.get_running_loop())
        _atv_name = resolved
        return _atv


async def _drop_connection() -> None:
    global _atv, _atv_name
    async with _lock:
        if _atv is not None:
            try:
                _atv.close()
            except Exception:
                pass
            _atv = None
            _atv_name = None


async def _run(
    call, device_name: str | None = None, idempotent: bool = False
) -> str:
    """Run a pyatv call against the cached connection.

    Tools always return a string: the result, or 'Error: ...'. Only
    idempotent (read-only) calls are retried after a reconnect — actions
    are not, because a failed ack can mean the action already happened
    and repeating it would double-press or double-queue."""
    try:
        atv = await _get_atv(device_name)
        return await call(atv)
    except Exception as first:
        # The connection just provably errored — always drop it so the
        # next call cold-connects instead of hitting the corpse. Only
        # idempotent calls retry inline; actions must not double-execute.
        await _drop_connection()
        if not idempotent:
            return f"Error: {type(first).__name__}: {first}"
        try:
            atv = await _get_atv(device_name)
            return await call(atv)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"


# ----------------------------------------------------------------- tools

@mcp.tool()
async def atv_remote(action: str, device: str | None = None) -> str:
    """Press a remote-control button. One of: up, down, left, right,
    select, menu (back), home, top_menu, home_hold, control_center,
    guide, screensaver, play, pause, play_pause, stop, next, previous,
    skip_forward, skip_backward, channel_up, channel_down, volume_up,
    volume_down, suspend (sleep), wakeup. Never blind-press buttons to
    'confirm a dialog' — you cannot see the TV screen."""
    action = action.lower().strip()
    if action not in REMOTE_ACTIONS:
        return f"Unknown action '{action}'. Valid: {', '.join(REMOTE_ACTIONS)}"

    async def call(atv):
        await getattr(atv.remote_control, action)()
        return f"Pressed {action}"

    return await _run(call, device)


@mcp.tool()
async def atv_volume(level: str | None = None, device: str | None = None) -> str:
    """Get or set volume. level: a number 0-100 to set, 'up'/'down' to
    step, or omit to read the current volume."""
    async def call(atv):
        if level is None or level == "get":
            return f"volume={atv.audio.volume:.0f}"
        value = level.lower().strip()
        if value == "up":
            await atv.audio.volume_up()
            return f"volume={atv.audio.volume:.0f}"
        if value == "down":
            await atv.audio.volume_down()
            return f"volume={atv.audio.volume:.0f}"
        try:
            numeric = float(value)
        except ValueError:
            return "level must be 0-100, 'up', 'down', or omitted"
        if not 0 <= numeric <= 100:
            return "level must be between 0 and 100"
        await atv.audio.set_volume(numeric)
        return f"volume set to {numeric:.0f}"

    return await _run(call, device)


@mcp.tool()
async def atv_seek(seconds: int, device: str | None = None) -> str:
    """Seek to a position (in seconds) in the currently playing media."""
    async def call(atv):
        await atv.remote_control.set_position(seconds)
        return f"Seeked to {seconds}s"

    return await _run(call, device)


@mcp.tool()
async def atv_repeat(mode: str, device: str | None = None) -> str:
    """Set repeat mode: off, track, or all."""
    mode = mode.lower().strip()
    if mode not in REPEAT_MODES:
        return f"Unknown mode '{mode}'. Valid: {', '.join(REPEAT_MODES)}"

    async def call(atv):
        await atv.remote_control.set_repeat(REPEAT_MODES[mode])
        return f"Repeat: {mode}"

    return await _run(call, device)


@mcp.tool()
async def atv_shuffle(mode: str, device: str | None = None) -> str:
    """Set shuffle mode: off, songs, or albums."""
    mode = mode.lower().strip()
    if mode not in SHUFFLE_MODES:
        return f"Unknown mode '{mode}'. Valid: {', '.join(SHUFFLE_MODES)}"

    async def call(atv):
        await atv.remote_control.set_shuffle(SHUFFLE_MODES[mode])
        return f"Shuffle: {mode}"

    return await _run(call, device)


@mcp.tool()
async def atv_audio_outputs(
    select: str | None = None, device: str | None = None
) -> str:
    """List AirPlay audio outputs (e.g. HomePods), or route audio to one
    by passing select='<name>'."""
    async def call(atv):
        devices = atv.audio.output_devices
        if select is None:
            if not devices:
                return "No audio outputs found."
            return "\n".join(f"{d.name} — {d.identifier}" for d in devices)
        match = next(
            (d for d in devices if select.lower() in d.name.lower()), None
        )
        if match is None:
            names = ", ".join(d.name for d in devices) or "none"
            return f"No output matching '{select}'. Available: {names}"
        await atv.audio.set_output_devices([match.identifier])
        return f"Audio output set to {match.name}"

    return await _run(call, device, idempotent=(select is None))


@mcp.tool()
async def atv_launch_app(app: str, device: str | None = None) -> str:
    """Launch an app by friendly name (netflix, youtube, disney+, max, hulu,
    prime video, peacock, paramount+, plex, spotify, tv, music, settings,
    app store, ...) or by bundle identifier."""
    bundle_id = APP_ALIASES.get(app.lower().strip(), app)

    async def call(atv):
        await atv.apps.launch_app(bundle_id)
        return f"Launched {app} ({bundle_id})"

    return await _run(call, device)


@mcp.tool()
async def atv_open_url(url: str, device: str | None = None) -> str:
    """Open a deep link on the Apple TV; tvOS routes it to the app that
    claims it. The way to pull up a specific movie or episode directly.
    Working shapes: https://tv.apple.com/us/show|movie|episode/... (Apple
    TV+), https://www.disneyplus.com/video/<uuid>, https://play.hbomax.com/
    page/urn:hbo:page:<id>, https://www.hulu.com/watch/<id>,
    https://watch.amazon.com/detail?gti=<id>, youtube://www.youtube.com/
    watch?v=<id> (may show a confirmation a human must accept). Netflix
    links are unreliable since its Sept 2025 app update."""
    async def call(atv):
        await atv.apps.launch_app(url)
        return f"Opened {url}"

    return await _run(call, device)


@mcp.tool()
async def atv_watch(
    title: str, service: str | None = None, device: str | None = None
) -> str:
    """Find a movie or show and pull it up on the Apple TV. Searches
    JustWatch, picks the deep link for the requested service (e.g.
    'netflix', 'disney+', 'apple tv', 'max', 'hulu', 'prime'), and opens
    it. Without a service, picks the first subscription-streaming offer.
    Region: ATV_JW_COUNTRY/ATV_JW_LANGUAGE env vars (default US/en)."""
    offers = await _justwatch_search(title)
    if offers is None:
        return (
            "JustWatch lookup failed (network or API change). Fallback: "
            "atv_launch_app + atv_type to search inside the app, or find "
            "a URL yourself and use atv_open_url."
        )
    if not offers:
        return (
            f"No streaming deep link found for '{title}'. Fallback: "
            "atv_launch_app + atv_type, or atv_open_url with a URL."
        )
    if service:
        wanted = service.lower().strip()
        chosen = next(
            (o for o in offers if wanted in o["provider"].lower()), None
        )
        if chosen is None:
            available = ", ".join(o["provider"] for o in offers)
            return (
                f"'{title}' not found on {service}. Available on: "
                f"{available}. Call again with one of those, or none."
            )
    else:
        chosen = offers[0]

    async def open_title(atv):
        await atv.apps.launch_app(chosen["url"])
        return f"Pulling up '{title}' via {chosen['provider']}.\nURL: {chosen['url']}"

    return await _run(open_title, device)


@mcp.tool()
async def atv_youtube(
    query: str, app: bool = False, device: str | None = None
) -> str:
    """Play a YouTube video on the TV. Accepts a URL (watch, youtu.be,
    shorts, embed, live), an 11-character video id, or plain search terms
    (the top result plays). By default plays full-screen in the system
    player via AirPlay. Set app=True to open in the YouTube app instead —
    tvOS may then show an 'Open in YouTube' confirmation that a human
    must accept with the physical remote."""
    query = query.strip()
    video_id = None
    match = re.search(
        r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})", query
    )
    if match:
        video_id = match.group(1)
    elif re.fullmatch(r"[\w-]{11}", query):
        video_id = query  # probable bare ID; falls back to search on failure

    try:
        vid, title, stream_url = await asyncio.get_running_loop(
        ).run_in_executor(None, _resolve_youtube, video_id, query)
    except Exception as e:
        return f"Error resolving video: {type(e).__name__}: {e}"

    if app:
        url = f"youtube://www.youtube.com/watch?v={vid}"

        async def open_in_app(atv):
            await atv.apps.launch_app(url)
            return (
                f"Opened in YouTube app: {url}\n"
                "(If a confirmation dialog appears, a human must accept "
                "it with the physical remote.)"
            )

        return await _run(open_in_app, device)

    async def call(atv):
        return await _play_stream_call(atv, title, stream_url)

    return await _run(call, device)


@mcp.tool()
async def atv_play(url: str, device: str | None = None) -> str:
    """Play any video URL on the TV in the native system player: direct
    media files (.mp4, .m3u8, .mov, ...) or a video page from any site
    yt-dlp supports (~1800: Vimeo, Twitch, X, news sites, most embed
    players). The stream is extracted, then queued on the TV via
    AirPlay — playback continues after the tool returns. DRM-protected
    services (Netflix, Disney+, ...) cannot be extracted; use atv_watch
    or atv_open_url for those."""
    try:
        title, stream_url = await asyncio.get_running_loop(
        ).run_in_executor(None, _page_resolve, url)
    except Exception as e:
        return f"Error resolving video: {type(e).__name__}: {e}"

    async def call(atv):
        prefix = f"Found: {title}\n" if title != url else ""
        return prefix + await _play_stream_call(atv, title, stream_url)

    return await _run(call, device)


@mcp.tool()
async def atv_list_apps(device: str | None = None) -> str:
    """List apps installed on the Apple TV (name + bundle identifier)."""
    async def call(atv):
        apps = await atv.apps.app_list()
        return "\n".join(f"{a.name} — {a.identifier}" for a in apps)

    return await _run(call, device, idempotent=True)


@mcp.tool()
async def atv_now_playing(device: str | None = None) -> str:
    """What is currently playing: title, artist, frontmost app, position,
    and playback state."""
    async def call(atv):
        p = await atv.metadata.playing()
        app = None
        try:
            app = atv.metadata.app
        except Exception:
            pass
        return (
            f"state={p.device_state} title={p.title} artist={p.artist} "
            f"album={p.album} app={app} position={p.position}/{p.total_time}s"
        )

    return await _run(call, device, idempotent=True)


@mcp.tool()
async def atv_type(
    text: str, clear: bool = False, device: str | None = None
) -> str:
    """Type text into the focused on-screen text field (e.g. a search box).
    The field must already be focused on screen. Set clear=True to empty
    the field first."""
    async def call(atv):
        if clear:
            await atv.keyboard.text_clear()
        await atv.keyboard.text_append(text)
        return f"Typed: {text}"

    return await _run(call, device)


@mcp.tool()
async def atv_power(action: str, device: str | None = None) -> str:
    """Power control: 'sleep' (turns off TV/AVR via HDMI-CEC) or 'wake'."""
    action = action.lower().strip()

    async def call(atv):
        if action in ("sleep", "off"):
            await atv.power.turn_off()
            return "Apple TV put to sleep"
        if action in ("wake", "on"):
            await atv.power.turn_on()
            return "Apple TV woken"
        return "Use action='sleep' or 'wake'"

    return await _run(call, device)


@mcp.tool()
async def atv_devices() -> str:
    """List paired Apple TVs and which is the default."""
    try:
        config = load_config()
    except RuntimeError as e:
        return f"Error: {e}"
    if not config["devices"]:
        return "No paired TVs. Run scripts/pair.py first."
    lines = []
    for name, d in config["devices"].items():
        marker = " (default)" if name == config.get("default") else ""
        lines.append(f"{name}{marker} — {d['address']}")
    return "\n".join(lines)


@mcp.tool()
async def atv_use(name: str) -> str:
    """Set the default Apple TV used when no device is specified."""
    try:
        config = load_config()
        if name not in config["devices"]:
            return (
                f"Unknown TV '{name}'. "
                f"Paired: {', '.join(config['devices'])}"
            )
        config["default"] = name
        save_config(config)
        return f"Default TV is now '{name}'"
    except RuntimeError as e:
        return f"Error: {e}"


@mcp.tool()
async def atv_scan() -> str:
    """Scan the network for Apple TVs (name, model, address)."""
    try:
        found = await pyatv.scan(asyncio.get_running_loop(), timeout=5.0)
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"
    if not found:
        return "No devices found."
    return "\n".join(
        f"{c.name} — {c.device_info.model or 'unknown'} at {c.address} "
        f"(id: {c.identifier})"
        for c in found
    )


# -------------------------------------------------------------- helpers

def _pick_stream(entry: dict, fallback_title: str) -> tuple[str, str]:
    """Pick (title, stream_url) from a yt-dlp info entry.

    Prefers the HLS master manifest: the Apple TV's native player
    handles adaptive variants and separate audio renditions itself.
    Falls back to the best progressive format (video+audio in one file).
    """
    formats = entry.get("formats", [])
    hls = next((f for f in formats if f.get("manifest_url")), None)
    if hls is not None:
        return entry.get("title", fallback_title), hls["manifest_url"]
    progressive = [
        f for f in formats
        if f.get("vcodec", "none") != "none"
        and f.get("acodec", "none") != "none"
        and f.get("url")
    ]
    if progressive:
        best = max(progressive, key=lambda f: f.get("height") or 0)
        return entry.get("title", fallback_title), best["url"]
    # Some extractors put a direct URL on the entry without formats.
    if entry.get("url") and not formats:
        return entry.get("title", fallback_title), entry["url"]
    raise RuntimeError(
        "No playable stream found (unsupported or DRM-protected page)"
    )


def _page_resolve(url: str) -> tuple[str, str]:
    """Resolve any video page URL to (title, stream_url).

    Direct media files skip extraction; everything else goes through
    yt-dlp, which supports ~1800 sites. DRM services (Netflix etc.)
    cannot be extracted — use atv_watch/atv_open_url for those."""
    if re.search(r"\.(mp4|m3u8|mov|m4v|webm)(\?|#|$)", url, re.I):
        return url, url
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        entry = (info.get("entries") or [info])[0]
        return _pick_stream(entry, url)


def _resolve_youtube(vid: str | None, terms: str):
    """Return (video_id, title, stream_url) for an id or search terms."""
    import yt_dlp

    opts = {"quiet": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        target = f"ytsearch1:{terms}" if vid is None else (
            f"https://www.youtube.com/watch?v={vid}"
        )
        try:
            info = ydl.extract_info(target, download=False)
        except Exception:
            if vid is None or vid != terms:
                raise
            # An 11-char search term misread as a bare video ID.
            info = ydl.extract_info(f"ytsearch1:{terms}", download=False)
        entry = (info.get("entries") or [info])[0]
        title, stream_url = _pick_stream(entry, "?")
        return entry["id"], title, stream_url


async def _play_stream_call(atv, title: str, stream_url: str) -> str:
    """Queue a stream via AirPlay and confirm it started.

    play_url blocks monitoring playback until the media ends — run it as
    a task, give the TV a few seconds to start, then return. Playback is
    queued on the TV itself and survives the tool returning."""
    play_task = asyncio.ensure_future(atv.stream.play_url(stream_url))

    def _log_late_failure(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            print(
                f"Playback ended with error: {task.exception()}",
                file=sys.stderr,
            )

    play_task.add_done_callback(_log_late_failure)
    await asyncio.sleep(8)
    if play_task.done() and play_task.exception() is not None:
        raise play_task.exception()
    return f"Playing: {title}"


async def _justwatch_search(query: str) -> list[dict] | None:
    """Unofficial JustWatch GraphQL search → [{provider, url}].

    Subscription streaming offers first, purchase/rental after, physical
    media removed, one offer per provider. Offers are merged across the
    top few title matches, so ambiguous queries can surface the wrong
    title's links — callers report what they're opening. Region comes
    from ATV_JW_COUNTRY/ATV_JW_LANGUAGE (default US/en). Returns None on
    transport/API failure, [] when the title simply has no offers."""
    gql = """
    query SearchTitles($searchTitlesFilter: TitleFilter!, $country: Country!,
                       $language: Language!, $first: Int!) {
      popularTitles(country: $country, filter: $searchTitlesFilter,
                    first: $first, sortBy: POPULAR) {
        edges {
          node {
            ... on MovieOrShow {
              content(country: $country, language: $language) { title }
              offers(country: $country, platform: WEB) {
                monetizationType
                package { clearName }
                standardWebURL
              }
            }
          }
        }
      }
    }
    """
    payload = {
        "query": gql,
        "variables": {
            "searchTitlesFilter": {"searchQuery": query},
            "country": os.environ.get("ATV_JW_COUNTRY", "US"),
            "language": os.environ.get("ATV_JW_LANGUAGE", "en"),
            "first": 3,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(JUSTWATCH_GRAPHQL, json=payload)
            resp.raise_for_status()
            data = resp.json()
        edges = data.get("data", {}).get("popularTitles", {}).get("edges", [])
        streaming, purchase, seen = [], [], set()
        for edge in edges:
            for offer in edge.get("node", {}).get("offers", []) or []:
                url = offer.get("standardWebURL")
                provider = offer.get("package", {}).get("clearName", "?")
                key = provider.lower()
                if not url or key in seen or key in PROVIDER_BLOCKLIST:
                    continue
                seen.add(key)
                entry = {"provider": provider, "url": url}
                if offer.get("monetizationType") in ("FLATRATE", "ADS", "FREE"):
                    streaming.append(entry)
                else:
                    purchase.append(entry)
        return streaming + purchase
    except Exception:
        return None


if __name__ == "__main__":
    mcp.run()
