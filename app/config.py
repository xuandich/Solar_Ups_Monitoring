from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LocalDeviceConfig:
    host: str
    name: str | None = None


@dataclass
class CloudConfig:
    token: str
    device_ids: list[str] = field(default_factory=list)


@dataclass
class ViewPowerDeviceConfig:
    port_name: str
    host: str = "localhost"
    port: int = 15178
    nominal_watts: float | None = None
    name: str | None = None


@dataclass
class AppConfig:
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    poll_interval: float = 2.0
    retention_days: int = 30
    chart_retention_days: int = 180
    raw_persist_interval_seconds: float = 120.0
    db_flush_interval_seconds: float = 600.0
    db_path: str = "mqsolar.db"
    storage_enabled: bool = True
    local_devices: list[LocalDeviceConfig] = field(default_factory=list)
    cloud: CloudConfig | None = None
    viewpower_devices: list[ViewPowerDeviceConfig] = field(default_factory=list)


def load_config(path: str | Path = "config.toml") -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file cấu hình '{path}'. "
            "Hãy copy config.example.toml thành config.toml rồi chỉnh sửa thông tin thiết bị."
        )

    with path.open("rb") as f:
        raw = tomllib.load(f)

    server = raw.get("server", {})
    polling = raw.get("polling", {})

    local_devices = [
        LocalDeviceConfig(host=d["host"], name=d.get("name"))
        for d in raw.get("local_devices", [])
    ]

    cloud_raw = raw.get("cloud")
    cloud = None
    if cloud_raw and cloud_raw.get("token"):
        cloud = CloudConfig(
            token=cloud_raw["token"],
            device_ids=list(cloud_raw.get("device_ids", [])),
        )

    viewpower_devices = [
        ViewPowerDeviceConfig(
            port_name=d["port_name"],
            host=d.get("host", "localhost"),
            port=int(d.get("port", 15178)),
            nominal_watts=(float(d["nominal_watts"]) if d.get("nominal_watts") is not None else None),
            name=d.get("name"),
        )
        for d in raw.get("viewpower_devices", [])
    ]

    if not local_devices and not cloud and not viewpower_devices:
        raise ValueError(
            "Cần cấu hình ít nhất 1 thiết bị trong [[local_devices]], [cloud] hoặc [[viewpower_devices]] trong config.toml"
        )

    return AppConfig(
        server_host=server.get("host", "0.0.0.0"),
        server_port=int(server.get("port", 8000)),
        poll_interval=float(polling.get("interval_seconds", 2)),
        retention_days=int(polling.get("retention_days", 30)),
        chart_retention_days=int(polling.get("chart_retention_days", 180)),
        raw_persist_interval_seconds=float(polling.get("raw_persist_interval_seconds", 120)),
        db_flush_interval_seconds=float(polling.get("db_flush_interval_seconds", 600)),
        db_path=polling.get("db_path", "mqsolar.db"),
        storage_enabled=bool(polling.get("storage_enabled", True)),
        local_devices=local_devices,
        cloud=cloud,
        viewpower_devices=viewpower_devices,
    )
