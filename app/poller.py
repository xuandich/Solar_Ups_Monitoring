from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import aiohttp

from .config import AppConfig, LocalDeviceConfig, ViewPowerDeviceConfig
from .mqsolar_client import MQSolarApiClient, MQSolarCloudClient
from .storage import Storage
from .viewpower_client import fetch_work_info, normalize_viewpower

_LOGGER = logging.getLogger(__name__)

# Thiết bị MPPT chỉ báo điện năng "hôm nay" và "tổng" — không có số theo tháng,
# và storage có thể đang tắt. Nên lưu mốc "tổng" đầu tháng ra file nhỏ này;
# điện năng tháng/năm = tổng hiện tại - mốc đầu tháng/năm.
MONTH_BASELINE_FILE = Path(__file__).resolve().parent.parent / "mqsolar_month.json"


class Poller:
    """Polls local devices / listens to the cloud websocket and keeps the latest
    reading per device in memory, while persisting snapshots to storage."""

    def __init__(self, config: AppConfig, storage: Storage):
        self.config = config
        self.storage = storage
        self.latest: dict[str, dict] = {}
        self._session: aiohttp.ClientSession | None = None
        self._tasks: list[asyncio.Task] = []
        self._cloud_client: MQSolarCloudClient | None = None
        self._last_persist: dict[str, float] = {}
        self._month_baseline: dict[str, dict] = self._load_month_baseline()

    @staticmethod
    def _load_month_baseline() -> dict[str, dict]:
        try:
            return json.loads(MONTH_BASELINE_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def _track_month(self, device_id: str, data: dict):
        """Gắn charger.powerMonth / powerYear (kWh từ đầu tháng / đầu năm) vào dữ liệu MPPT.

        Sang tháng/năm mới (hoặc lần đầu thấy thiết bị) mốc = total - today của lần
        đọc đầu tiên, tức tổng lúc bắt đầu ngày đó — chính xác nếu service chạy
        từ ngày 1; nếu bật muộn hơn thì thiếu phần các ngày trước đó.
        """
        charger = data.get("charger")
        if not charger:
            return
        total, today = charger.get("powerTotal"), charger.get("powerToday")
        if total is None or today is None:
            return
        month, year = time.strftime("%Y-%m"), time.strftime("%Y")
        entry = self._month_baseline.setdefault(device_id, {})
        changed = False
        if entry.get("month") != month:
            entry["month"], entry["baseline"] = month, round(total - today, 3)
            changed = True
        if entry.get("year") != year:
            entry["year"], entry["yearBaseline"] = year, round(total - today, 3)
            changed = True
        if changed:
            try:
                MONTH_BASELINE_FILE.write_text(json.dumps(self._month_baseline, indent=2))
            except OSError as e:
                _LOGGER.warning("Could not save month baseline: %s", e)
        charger["powerMonth"] = round(max(0.0, total - entry["baseline"]), 3)
        charger["powerYear"] = round(max(0.0, total - entry["yearBaseline"]), 3)

    async def _maybe_persist(self, device_id: str, mode: str, data: dict):
        """Ghi xuống DB tối đa 1 lần mỗi raw_persist_interval_seconds/thiết bị.

        RAM (self.latest, phục vụ /api/status) vẫn cập nhật mỗi lần poll
        (poll_interval, thường 2s) — chỉ THAO TÁC GHI ĐĨA bị hạ tần suất, để
        vừa giảm số lần ghi vừa giảm khối lượng dữ liệu thô tích luỹ.
        """
        if not self.config.storage_enabled:
            return
        if not data.get("hasData"):
            return
        now = time.time()
        if now - self._last_persist.get(device_id, 0) < self.config.raw_persist_interval_seconds:
            return
        self._last_persist[device_id] = now
        await self.storage.insert_reading(device_id, data.get("_device_type"), mode, data)

    async def start(self):
        self._session = aiohttp.ClientSession()

        for device in self.config.local_devices:
            self._tasks.append(asyncio.create_task(self._run_local(device)))

        if self.config.cloud:
            self._cloud_client = MQSolarCloudClient(
                self.config.cloud.token, self.config.cloud.device_ids, self._session
            )
            await self._cloud_client.connect()
            self._tasks.append(asyncio.create_task(self._run_cloud()))

        for vp_cfg in self.config.viewpower_devices:
            self._tasks.append(asyncio.create_task(self._run_viewpower(vp_cfg)))

        if self.config.storage_enabled:
            self._tasks.append(asyncio.create_task(self._run_cleanup()))
            self._tasks.append(asyncio.create_task(self._run_rollup()))

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self._cloud_client:
            await self._cloud_client.stop()
        if self._session:
            await self._session.close()

    async def _run_local(self, device: LocalDeviceConfig):
        api = MQSolarApiClient(device.host, self._session)
        while True:
            try:
                data = await api.fetch_data()
                device_id = data.get("_device_id") or device.host
                data["_name"] = device.name or device_id
                self._track_month(device_id, data)
                self.latest[device_id] = data
                await self._maybe_persist(device_id, "local", data)
            except Exception as e:
                _LOGGER.warning("Local poll failed for %s: %s", device.host, e)

            await asyncio.sleep(self.config.poll_interval)

    async def _run_cloud(self):
        while True:
            for device_id, data in list(self._cloud_client.data.items()):
                self._track_month(device_id, data)
                self.latest[device_id] = data
                await self._maybe_persist(device_id, "cloud", data)
            await asyncio.sleep(self.config.poll_interval)

    async def _run_viewpower(self, vp_cfg: ViewPowerDeviceConfig):
        device_id = vp_cfg.port_name
        while True:
            try:
                work_info = await fetch_work_info(vp_cfg.host, vp_cfg.port, vp_cfg.port_name, self._session)
                data = normalize_viewpower(work_info, nominal_watts=vp_cfg.nominal_watts)
                data["_device_id"] = device_id
                data["_name"] = vp_cfg.name or f"UPS {device_id}"
                self.latest[device_id] = data

                # UPS: chỉ lưu lịch sử + cho biểu đồ mục Load, các trường
                # khác (battery charge, voltages, nhiệt độ...) chỉ hiển thị
                # live (self.latest), không cần lưu lâu dài xuống đĩa.
                store_data = {
                    "ups": {
                        "load": data["ups"].get("load"),
                        "loadWatts": data["ups"].get("loadWatts"),
                    },
                    "hasData": data.get("hasData"),
                    "_device_type": data.get("_device_type"),
                }
                await self._maybe_persist(device_id, "viewpower", store_data)
            except Exception as e:
                _LOGGER.warning("ViewPower poll failed for %s@%s: %s", vp_cfg.port_name, vp_cfg.host, e)

            await asyncio.sleep(self.config.poll_interval)

    async def _run_cleanup(self):
        while True:
            await asyncio.sleep(3600)
            try:
                await self.storage.cleanup_old(self.config.retention_days)
                await self.storage.cleanup_old_charts(self.config.chart_retention_days)
            except Exception as e:
                _LOGGER.warning("Cleanup failed: %s", e)

    async def _run_rollup(self):
        """Tổng hợp dữ liệu thô thành trung bình theo giờ/ngày cho biểu đồ lịch sử."""
        while True:
            try:
                await self.storage.run_rollups()
            except Exception as e:
                _LOGGER.warning("Rollup failed: %s", e)
            await asyncio.sleep(600)
