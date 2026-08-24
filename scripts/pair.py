"""One-time pairing helper: register an Apple TV for appletv-mcp.

Pairs the Companion and AirPlay protocols and stores credentials in
~/.config/appletv-remote/devices.json (shared with the appletv-remote
skill, owner-only permissions).

Interactive:
    uv run python scripts/pair.py [name-filter]

Non-interactive (agent-driven): set ATV_PIN_FILE=/tmp/atv_pin.txt and
write each PIN shown on the TV to that file; a name filter is required
when more than one device is found.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from pyatv import pair as atv_pair
from pyatv import scan as atv_scan
from pyatv.const import Protocol

CONFIG_DIR = Path.home() / ".config" / "appletv-remote"
CONFIG_PATH = CONFIG_DIR / "devices.json"
PIN_FILE = os.environ.get("ATV_PIN_FILE")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"default": None, "devices": {}}
    try:
        config = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        sys.exit(
            f"Config at {CONFIG_PATH} is corrupt ({e}). "
            "Delete it and re-run pair."
        )
    if not isinstance(config, dict):
        sys.exit(
            f"Config at {CONFIG_PATH} has an unexpected shape. "
            "Delete it and re-run pair."
        )
    config.setdefault("default", None)
    config.setdefault("devices", {})
    return config


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(CONFIG_PATH, 0o600)  # credentials — owner read/write only


async def wait_for_pin_file(protocol_name: str) -> str:
    print(
        f"[{protocol_name}] PIN shown on TV. Waiting for it in {PIN_FILE} ...",
        flush=True,
    )
    for _ in range(600):
        if os.path.exists(PIN_FILE):
            pin = Path(PIN_FILE).read_text().strip()
            os.remove(PIN_FILE)
            if pin:
                return pin
        await asyncio.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for PIN in {PIN_FILE}")


async def main() -> None:
    name_filter = sys.argv[1] if len(sys.argv) > 1 else None
    loop = asyncio.get_running_loop()
    found = await atv_scan(loop, timeout=5.0)
    tvs = [c for c in found if c.device_info.model is not None]
    if not tvs:
        sys.exit("No devices found. Is the Apple TV on this network?")

    print("Devices on the network:")
    for i, c in enumerate(tvs):
        print(f"  [{i}] {c.name} — {c.device_info.model} at {c.address}")

    if name_filter:
        target = next(
            (c for c in tvs if c.name and name_filter.lower() in c.name.lower()),
            None,
        )
        if target is None:
            sys.exit(f"No device matching '{name_filter}' in the list above.")
    elif len(tvs) == 1:
        target = tvs[0]
    elif PIN_FILE:
        sys.exit(
            "Multiple devices found; in ATV_PIN_FILE mode you must pass a "
            "name filter: pair.py <name>"
        )
    else:
        choice = input("Number to pair: ").strip()
        try:
            target = tvs[int(choice)]
        except (ValueError, IndexError):
            sys.exit(f"'{choice}' is not a valid choice.")

    print(f"Pairing with {target.name} at {target.address} ...")
    credentials = {}

    for protocol in (Protocol.Companion, Protocol.AirPlay):
        if target.get_service(protocol) is None:
            continue
        pairing = await atv_pair(target, protocol, loop)
        try:
            await pairing.begin()
            if PIN_FILE:
                pin = await wait_for_pin_file(protocol.name)
            else:
                pin = input(f"[{protocol.name}] PIN shown on TV: ").strip()
            if not pin.isdigit():
                sys.exit(f"Invalid PIN '{pin}' — expected digits.")
            pairing.pin(int(pin))
            await pairing.finish()
        finally:
            await pairing.close()
        if not pairing.has_paired:
            sys.exit(f"[{protocol.name}] Pairing failed — try again.")
        credentials[protocol.name.lower()] = pairing.service.credentials
        print(f"[{protocol.name}] paired.")

    config = load_config()
    name = target.name or f"tv-{len(config['devices']) + 1}"
    if name in config["devices"]:
        print(f"Note: re-pairing existing TV '{name}', updating credentials")
    config["devices"][name] = {
        "address": str(target.address),
        "identifier": target.identifier or str(target.address),
        "credentials": credentials,
    }
    if not config.get("default"):
        config["default"] = name
    save_config(config)
    print(f"Saved '{name}' to {CONFIG_PATH} (default: {config['default']})")
    print("Keep that file private — it grants full control of the TV.")


if __name__ == "__main__":
    asyncio.run(main())
