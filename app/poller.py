from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import aiohttp

from . import cloud_sync, drive_sync
from .config import AppConfig, LocalDeviceConfig, ViewPowerDeviceConfig
from .energy import EnergyTracker
from .mqsolar_client import MQSolarApiClient, MQSolarCloudClient
from .storage import Storage
from .viewpower_client import fetch_work_info, normalize_viewpower

_LOGGER = logging.getLogger(__name__)


class _NoDevice(Exception):
    """Chưa đọc được thiết bị MPPT nào (vd: vừa bật máy, mạng/thiết bị chưa sẵn sàng)."""

# Điện năng thực vào ắc quy tự tích phân + vá khi tạm dừng: xem app/energy.py.
ENERGY_FILE = Path(__file__).resolve().parent.parent / "mqsolar_energy.json"


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
        self.energy = EnergyTracker(ENERGY_FILE)
        self._sync_lock = asyncio.Lock()
        self.last_sync: dict | None = None

    def _track_battery_energy(self, device_id: str, data: dict):
        """Gắn batToday/batMonth/batYear/batTotal (kWh thực vào ắc quy) vào dữ liệu MPPT."""
        charger = data.get("charger")
        if not charger:
            return
        totals = self.energy.update(device_id, charger)
        if totals:
            charger["batToday"], charger["batMonth"] = totals["today"], totals["month"]
            charger["batYear"], charger["batTotal"] = totals["year"], totals["total"]

    async def _sync_cloud_history(self):
        """Kéo thống kê ngày/giờ từ cloud để vá dữ liệu khi máy không chạy."""
        token = self.config.cloud_api_token
        device_ids = [d for d, v in self.latest.items() if v.get("charger")]
        if not device_ids:
            raise _NoDevice("chưa đọc được thiết bị MPPT nào, sẽ thử lại")
        now = time.time()
        summary = {"devices": 0, "days": 0, "hours": 0, "filled": 0}
        for device_id in device_ids:
            rows = await cloud_sync.fetch_stats(
                self._session, token, device_id, "1d", now - cloud_sync.DAILY_LOOKBACK_DAYS * 86400, now + 86400
            )
            self.energy.apply_cloud_days(device_id, cloud_sync.daily_kwh(rows))
            summary["days"] += len(rows)

            filled = hourly = 0
            if self.config.storage_enabled:
                device_type = self.latest[device_id].get("_device_type")
                rows = await cloud_sync.fetch_stats(
                    self._session, token, device_id, "1h", now - cloud_sync.HOURLY_LOOKBACK_DAYS * 86400, now + 3600
                )
                days = set()
                hourly = len(rows)
                for r in rows:
                    bucket, data = cloud_sync.hourly_row(r, device_type)
                    if await self.storage.upsert_cloud_hourly(device_id, str(device_type), "local", bucket, data, self.config.raw_persist_interval_seconds):
                        filled += 1
                        days.add(bucket - bucket % 86400)
                for day in days:
                    await self.storage.rollup_day(device_id, str(device_type), "local", day)
            summary["devices"] += 1
            summary["hours"] += hourly
            summary["filled"] += filled
            _LOGGER.info("Cloud sync %s: vá %d/%d giờ vào DB, cập nhật số liệu ngày", device_id, filled, hourly)
        return summary

    async def _sync_drive_backup(self):
        """Đọc bản sao lưu trên Google Drive: kiểm tra chéo với DB local và vá số liệu còn thiếu."""
        cfg = self.config
        device_ids = [d for d, v in self.latest.items() if v.get("charger")]
        if not device_ids:
            raise _NoDevice("chưa đọc được thiết bị MPPT nào, sẽ thử lại")
        now = time.time()
        summary = {"devices": 0, "hours": 0, "days": 0, "filled": 0, "checked": 0, "mismatched": 0}
        for device_id in device_ids:
            body = await drive_sync.fetch_backup(
                self._session, cfg.drive_backup_url, cfg.drive_backup_key, device_id,
                since=now - cloud_sync.HOURLY_LOOKBACK_DAYS * 86400,
            )
            hourly_rows, daily_rows = list(body.get("hourly", {}).values()), list(body.get("daily", {}).values())
            self.energy.apply_cloud_days(device_id, cloud_sync.daily_kwh(daily_rows))
            filled = checked = mismatched = 0
            if cfg.storage_enabled:
                device_type = self.latest[device_id].get("_device_type")
                days = set()
                for r in hourly_rows:
                    bucket, data = cloud_sync.hourly_row(r, device_type)
                    if await self.storage.upsert_cloud_hourly(device_id, str(device_type), "local", bucket, data, self.config.raw_persist_interval_seconds):
                        filled += 1
                        days.add(bucket - bucket % 86400)
                        continue
                    local = await self.storage.get_hourly(device_id, bucket)
                    if not local or local.get("_scaled"):
                        continue   # dòng này vốn từ cloud/Drive, không phải số đo local độc lập
                    a = (local.get("charger") or {}).get("chargingPower")
                    b = data["charger"].get("chargingPower")
                    if a is None or b is None:
                        continue
                    checked += 1
                    if abs(a - b) > max(30.0, 0.25 * max(a, b)):
                        mismatched += 1
                        _LOGGER.warning("Drive vs local lệch giờ %s: local %.0f W, Drive %.0f W",
                                        time.strftime("%d/%m %H:00", time.localtime(bucket)), a, b)
                for day in days:
                    await self.storage.rollup_day(device_id, str(device_type), "local", day)
            _LOGGER.info("Drive backup %s: %d giờ / %d ngày; vá %d giờ; so khớp %d giờ (lệch %d)",
                         device_id, len(hourly_rows), len(daily_rows), filled, checked, mismatched)
            for k, v in (("devices", 1), ("hours", len(hourly_rows)), ("days", len(daily_rows)),
                         ("filled", filled), ("checked", checked), ("mismatched", mismatched)):
                summary[k] += v
        return summary

    async def sync_now(self) -> dict:
        """Đồng bộ ngay theo yêu cầu (nút trên dashboard): cloud REST API + bản sao Google Drive.

        Chỉ gọi cloud/Google, KHÔNG gửi gì tới thiết bị MPPT. Một lượt tại một thời điểm.
        """
        has_cloud = bool(self.config.cloud_api_token)
        has_drive = bool(self.config.drive_backup_url and self.config.drive_backup_key)
        if not (has_cloud or has_drive):
            return {"ok": False, "configured": False, "errors": {"config": "Chưa cấu hình [cloud_api] hoặc [drive_backup]"}}
        if self._sync_lock.locked():
            return {"ok": False, "busy": True, "errors": {"busy": "Đang có một lượt đồng bộ khác"}}
        async with self._sync_lock:
            result: dict = {"cloud": None, "drive": None, "errors": {}}
            for name, enabled, fn in (("cloud", has_cloud, self._sync_cloud_history),
                                      ("drive", has_drive, self._sync_drive_backup)):
                if not enabled:
                    continue
                try:
                    result[name] = await fn()
                except Exception as e:
                    result["errors"][name] = str(e)[:200]
                    _LOGGER.warning("Đồng bộ thủ công (%s) thất bại: %s", name, e)
            result["ok"] = not result["errors"]
            result["at"] = time.time()
            self.last_sync = result
            return result

    async def _run_cloud_sync(self):
        """Đồng bộ khi khởi động và sau đó cloud_sync_per_day lần mỗi ngày (mặc định 4 = 6 giờ/lần):
        cloud REST API (nếu có token) và bản sao Google Drive (nếu có [drive_backup]).

        Lịch tính theo GIỜ THỰC (time.time) và kiểm tra mỗi 30 giây, không dùng một lần
        asyncio.sleep dài: bộ đếm của asyncio không chạy khi máy ngủ (suspend) nên lượt
        đồng bộ sẽ bị trễ hàng giờ. Phát hiện máy vừa thức dậy (đồng hồ nhảy quá 2 phút
        giữa hai lần kiểm tra) thì đồng bộ sớm, sau ~30 giây để mạng kịp kết nối lại.
        """
        interval = 86400 / self.config.cloud_sync_per_day
        tick = 30.0
        await asyncio.sleep(30)   # chờ vòng poll đầu tiên có dữ liệu
        next_due = time.time()
        last_check = time.time()
        while True:
            now = time.time()
            if now - last_check > 4 * tick:   # chỉ đo quãng ngủ giữa 2 lần kiểm tra (không tính thời gian đồng bộ)
                next_due = min(next_due, now + tick)
                _LOGGER.info("Phát hiện máy vừa thức dậy sau %.0f phút, sẽ đồng bộ sớm", (now - last_check) / 60)
            if now >= next_due:
                ok, retry = True, 600
                async with self._sync_lock:   # chung khoá với nút đồng bộ thủ công, không chạy song song
                    for enabled, label, fn in (
                        (bool(self.config.cloud_api_token), "Cloud sync", self._sync_cloud_history),
                        (bool(self.config.drive_backup_url and self.config.drive_backup_key), "Drive backup sync", self._sync_drive_backup),
                    ):
                        if not enabled:
                            continue
                        try:
                            await fn()
                        except _NoDevice as e:
                            ok, retry = False, 60
                            _LOGGER.info("%s: %s", label, e)
                        except Exception as e:
                            ok = False
                            _LOGGER.warning("%s thất bại, thử lại sau %d phút: %s", label, retry // 60, e)
                next_due = time.time() + (interval if ok else retry)
            last_check = time.time()
            await asyncio.sleep(tick)

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

        if self.config.cloud_api_token or (self.config.drive_backup_url and self.config.drive_backup_key):
            self._tasks.append(asyncio.create_task(self._run_cloud_sync()))

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
        self.energy.save()

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
                self._track_battery_energy(device_id, data)
                self.latest[device_id] = data
                await self._maybe_persist(device_id, "local", data)
            except Exception as e:
                _LOGGER.warning("Local poll failed for %s: %s", device.host, e)

            await asyncio.sleep(self.config.poll_interval)

    async def _run_cloud(self):
        while True:
            for device_id, data in list(self._cloud_client.data.items()):
                self._track_battery_energy(device_id, data)
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
