"""Behavior and metadata tests for every public Apple TV MCP tool."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp import Client
from pyatv.const import RepeatState, ShuffleState

import server as app

EXPECTED_ANNOTATIONS = {
    "atv_remote": (False, False, False, False),
    "atv_volume": (False, False, False, False),
    "atv_seek": (False, False, True, False),
    "atv_repeat": (False, False, True, False),
    "atv_shuffle": (False, False, True, False),
    "atv_audio_outputs": (False, False, True, False),
    "atv_launch_app": (False, False, False, False),
    "atv_open_url": (False, False, False, True),
    "atv_watch": (False, False, False, True),
    "atv_youtube": (False, False, False, True),
    "atv_play": (False, False, False, True),
    "atv_list_apps": (True, False, True, False),
    "atv_now_playing": (True, False, True, False),
    "atv_type": (False, False, False, False),
    "atv_power": (False, False, True, False),
    "atv_devices": (True, False, True, False),
    "atv_use": (False, False, True, False),
    "atv_scan": (True, False, True, False),
}


class FakeRemoteControl:
    def __init__(self) -> None:
        self.action: str | None = None
        self.position: int | None = None
        self.repeat: RepeatState | None = None
        self.shuffle: ShuffleState | None = None

    async def up(self) -> None:
        self.action = "up"

    async def set_position(self, seconds: int) -> None:
        self.position = seconds

    async def set_repeat(self, mode: RepeatState) -> None:
        self.repeat = mode

    async def set_shuffle(self, mode: ShuffleState) -> None:
        self.shuffle = mode


class FakeAudio:
    def __init__(self) -> None:
        self.volume = 25.0
        self.output_devices = [
            SimpleNamespace(name="Living Room", identifier="living"),
            SimpleNamespace(name="Bedroom HomePod", identifier="bedroom"),
        ]
        self.selected_output_ids: list[str] = []

    async def set_volume(self, value: float) -> None:
        self.volume = value

    async def volume_up(self) -> None:
        self.volume += 1

    async def volume_down(self) -> None:
        self.volume -= 1

    async def set_output_devices(self, identifiers: list[str]) -> None:
        self.selected_output_ids = identifiers


class FakeApps:
    def __init__(self) -> None:
        self.launched: str | None = None

    async def launch_app(self, target: str) -> None:
        self.launched = target

    async def app_list(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="TV", identifier="com.apple.TVWatchList")]


class FakeMetadata:
    app = "TV"

    async def playing(self) -> SimpleNamespace:
        return SimpleNamespace(
            device_state="playing",
            title="Severance",
            artist="",
            album="Season 2",
            position=120,
            total_time=3000,
        )


class FakeKeyboard:
    def __init__(self) -> None:
        self.text = "existing"

    async def text_clear(self) -> None:
        self.text = ""

    async def text_append(self, text: str) -> None:
        self.text += text


class FakePower:
    def __init__(self) -> None:
        self.state = "on"

    async def turn_off(self) -> None:
        self.state = "off"

    async def turn_on(self) -> None:
        self.state = "on"


class FakeAtv:
    def __init__(self) -> None:
        self.remote_control = FakeRemoteControl()
        self.audio = FakeAudio()
        self.apps = FakeApps()
        self.metadata = FakeMetadata()
        self.keyboard = FakeKeyboard()
        self.power = FakePower()
        self.requested_device: str | None = None
        self.played: tuple[str, str] | None = None


@pytest.fixture
async def client():
    async with Client(app.mcp) as mcp_client:
        yield mcp_client


@pytest.fixture
def fake_atv(monkeypatch: pytest.MonkeyPatch) -> FakeAtv:
    fake = FakeAtv()

    async def get_atv(device: str | None) -> FakeAtv:
        fake.requested_device = device
        return fake

    monkeypatch.setattr(app, "_get_atv", get_atv)
    return fake


async def call(client: Client, tool_name: str, **arguments: object) -> str:
    result = await client.call_tool(tool_name, arguments)
    assert isinstance(result.data, str)
    return result.data


async def test_all_tools_publish_explicit_behavior_annotations(client: Client) -> None:
    tools = await client.list_tools()
    actual = {tool.name: tool.annotations.model_dump(by_alias=True) for tool in tools}

    assert set(actual) == set(EXPECTED_ANNOTATIONS)
    for name, expected in EXPECTED_ANNOTATIONS.items():
        values = actual[name]
        observed = (
            values["readOnlyHint"],
            values["destructiveHint"],
            values["idempotentHint"],
            values["openWorldHint"],
        )
        assert all(type(value) is bool for value in observed)
        assert observed == expected


async def test_atv_remote_presses_requested_button(
    client: Client, fake_atv: FakeAtv
) -> None:
    result = await call(client, "atv_remote", action="up", device="Living Room")

    assert result == "Pressed up"
    assert fake_atv.remote_control.action == "up"
    assert fake_atv.requested_device == "Living Room"


async def test_atv_volume_sets_absolute_level(
    client: Client, fake_atv: FakeAtv
) -> None:
    result = await call(client, "atv_volume", level="37")

    assert result == "volume set to 37"
    assert fake_atv.audio.volume == 37.0


async def test_atv_seek_sets_playback_position(
    client: Client, fake_atv: FakeAtv
) -> None:
    result = await call(client, "atv_seek", seconds=42)

    assert result == "Seeked to 42s"
    assert fake_atv.remote_control.position == 42


async def test_atv_repeat_sets_repeat_mode(client: Client, fake_atv: FakeAtv) -> None:
    result = await call(client, "atv_repeat", mode="all")

    assert result == "Repeat: all"
    assert fake_atv.remote_control.repeat is RepeatState.All


async def test_atv_shuffle_sets_shuffle_mode(client: Client, fake_atv: FakeAtv) -> None:
    result = await call(client, "atv_shuffle", mode="albums")

    assert result == "Shuffle: albums"
    assert fake_atv.remote_control.shuffle is ShuffleState.Albums


async def test_atv_audio_outputs_routes_to_matching_device(
    client: Client, fake_atv: FakeAtv
) -> None:
    result = await call(client, "atv_audio_outputs", select="bedroom")

    assert result == "Audio output set to Bedroom HomePod"
    assert fake_atv.audio.selected_output_ids == ["bedroom"]


async def test_atv_launch_app_resolves_friendly_name(
    client: Client, fake_atv: FakeAtv
) -> None:
    result = await call(client, "atv_launch_app", app="netflix")

    assert result == "Launched netflix (com.netflix.Netflix)"
    assert fake_atv.apps.launched == "com.netflix.Netflix"


async def test_atv_open_url_launches_deep_link(
    client: Client, fake_atv: FakeAtv
) -> None:
    url = "https://tv.apple.com/us/show/example"

    result = await call(client, "atv_open_url", url=url)

    assert result == f"Opened {url}"
    assert fake_atv.apps.launched == url


async def test_atv_watch_opens_matching_streaming_offer(
    client: Client, fake_atv: FakeAtv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def justwatch_search(title: str) -> list[dict[str, str]]:
        assert title == "Severance"
        return [
            {"provider": "Apple TV Plus", "url": "https://tv.apple.com/show/severance"},
            {"provider": "Prime Video", "url": "https://watch.amazon.com/severance"},
        ]

    monkeypatch.setattr(app, "_justwatch_search", justwatch_search)

    result = await call(client, "atv_watch", title="Severance", service="apple tv")

    assert result.startswith("Pulling up 'Severance' via Apple TV Plus.")
    assert fake_atv.apps.launched == "https://tv.apple.com/show/severance"


async def test_atv_youtube_opens_resolved_video_in_app(
    client: Client, fake_atv: FakeAtv, monkeypatch: pytest.MonkeyPatch
) -> None:
    def resolve_youtube(video_id: str | None, terms: str) -> tuple[str, str, str]:
        assert video_id is None
        assert terms == "new Veritasium video"
        return "abcdefghijk", "Demo", "https://video.example/stream.m3u8"

    monkeypatch.setattr(app, "_resolve_youtube", resolve_youtube)

    result = await call(client, "atv_youtube", query="new Veritasium video", app=True)

    assert result.startswith(
        "Opened in YouTube app: youtube://www.youtube.com/watch?v=abcdefghijk"
    )
    assert fake_atv.apps.launched == "youtube://www.youtube.com/watch?v=abcdefghijk"


async def test_atv_play_resolves_and_queues_video(
    client: Client, fake_atv: FakeAtv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app,
        "_page_resolve",
        lambda url: ("Demo video", "https://cdn.example/demo.m3u8"),
    )

    async def play_stream(atv: FakeAtv, title: str, stream_url: str) -> str:
        atv.played = (title, stream_url)
        return f"Playing: {title}"

    monkeypatch.setattr(app, "_play_stream_call", play_stream)

    result = await call(client, "atv_play", url="https://example.com/watch/demo")

    assert result == "Found: Demo video\nPlaying: Demo video"
    assert fake_atv.played == ("Demo video", "https://cdn.example/demo.m3u8")


async def test_atv_list_apps_returns_installed_apps(
    client: Client, fake_atv: FakeAtv
) -> None:
    result = await call(client, "atv_list_apps")

    assert result == "TV — com.apple.TVWatchList"


async def test_atv_now_playing_returns_playback_metadata(
    client: Client, fake_atv: FakeAtv
) -> None:
    result = await call(client, "atv_now_playing")

    assert result == (
        "state=playing title=Severance artist= album=Season 2 app=TV position=120/3000s"
    )


async def test_atv_type_clears_then_enters_text(
    client: Client, fake_atv: FakeAtv
) -> None:
    result = await call(client, "atv_type", text="Severance", clear=True)

    assert result == "Typed: Severance"
    assert fake_atv.keyboard.text == "Severance"


async def test_atv_power_puts_tv_to_sleep(client: Client, fake_atv: FakeAtv) -> None:
    result = await call(client, "atv_power", action="sleep")

    assert result == "Apple TV put to sleep"
    assert fake_atv.power.state == "off"


async def test_atv_devices_lists_default_tv(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app,
        "load_config",
        lambda: {
            "default": "Living Room",
            "devices": {"Living Room": {"address": "192.168.1.10"}},
        },
    )

    result = await call(client, "atv_devices")

    assert result == "Living Room (default) — 192.168.1.10"


async def test_atv_use_persists_new_default(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "default": "Living Room",
        "devices": {
            "Living Room": {"address": "192.168.1.10"},
            "Bedroom": {"address": "192.168.1.11"},
        },
    }
    saved: list[dict] = []
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "save_config", lambda value: saved.append(value.copy()))

    result = await call(client, "atv_use", name="Bedroom")

    assert result == "Default TV is now 'Bedroom'"
    assert saved == [{**config, "default": "Bedroom"}]


async def test_atv_scan_returns_discovered_device(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = SimpleNamespace(
        name="Living Room",
        device_info=SimpleNamespace(model="Apple TV 4K"),
        address="192.168.1.10",
        identifier="device-id",
    )

    async def scan(loop, timeout: float) -> list[SimpleNamespace]:
        assert loop is not None
        assert timeout == 5.0
        return [found]

    monkeypatch.setattr(app.pyatv, "scan", scan)

    result = await call(client, "atv_scan")

    assert result == "Living Room — Apple TV 4K at 192.168.1.10 (id: device-id)"
