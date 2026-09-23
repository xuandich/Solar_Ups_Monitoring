"""Quét mạng LAN tìm thiết bị MQ Solar và ghi thêm vào config.toml.

Chạy: uv run python -m app.autoconfig
"""

from __future__ import annotations

import asyncio
import sys
import tomllib
from pathlib import Path

import aiohttp

from .discovery import scan_network

CONFIG_PATH = Path("config.toml")
EXAMPLE_PATH = Path("config.example.toml")


def _existing_hosts(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return {d["host"] for d in raw.get("local_devices", []) if "host" in d}


def _device_label(info: dict) -> str:
    dtype_raw = info.get("device_type")
    if str(dtype_raw) == "1":
        return "Inverter"
    return "MPPT Charger"


async def run() -> int:
    if not CONFIG_PATH.exists():
        if not EXAMPLE_PATH.exists():
            print("Không tìm thấy config.toml hay config.example.toml.", file=sys.stderr)
            return 1
        CONFIG_PATH.write_text(EXAMPLE_PATH.read_text())
        print(f"Đã tạo {CONFIG_PATH} từ {EXAMPLE_PATH}.")

    print("Đang quét mạng nội bộ tìm thiết bị MQ Solar...")
    async with aiohttp.ClientSession() as session:
        found = await scan_network(session)

    if not found:
        print("Không tìm thấy thiết bị nào trong mạng.")
        return 0

    existing = _existing_hosts(CONFIG_PATH)
    new_entries = {ip: info for ip, info in found.items() if ip not in existing}

    print(f"Tìm thấy {len(found)} thiết bị.")
    if not new_entries:
        print("Tất cả đã có trong config.toml, không có gì để thêm.")
        return 0

    lines = ["", "# --- Tự động thêm bởi app/autoconfig.py ---"]
    for ip, info in new_entries.items():
        label = _device_label(info)
        device_id = info.get("deviceId", "unknown")
        lines.append("[[local_devices]]")
        lines.append(f'host = "{ip}"')
        lines.append(f'name = "{label} ({device_id})"')
        lines.append("")

    with CONFIG_PATH.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    for ip, info in new_entries.items():
        print(f"  + Thêm {ip} -> {_device_label(info)}")
    print(f"Đã ghi {len(new_entries)} thiết bị mới vào {CONFIG_PATH}.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
