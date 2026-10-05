"""Đồng bộ lịch sử từ cloud REST API (thống kê theo ngày/giờ) để vá dữ liệu khi
máy không chạy. Chỉ dùng khi có [cloud_api] token trong config.toml.

kWh trên cloud (kwh_today/kwh_total) tính theo phía PV nên được nhân
BATTERY_SIDE_FACTOR trước khi dùng (xem app/energy.py).
"""
from __future__ import annotations

import calendar
import logging
import time

import aiohttp

from .energy import BATTERY_SIDE_FACTOR

_LOGGER = logging.getLogger(__name__)

API = "https://api.manhquansolar.io.vn/api"
# Cloudflare chặn User-Agent mặc định của thư viện HTTP (lỗi 1010).
HEADERS = {"User-Agent": "curl/8.5.0"}

DAILY_LOOKBACK_DAYS = 40
HOURLY_LOOKBACK_DAYS = 7

_FIELD_MAP = {
    "avg_pv_volt": "pvVoltage", "avg_pv_amp": "pvCurrent", "avg_bat_volt": "batVoltage",
    "avg_bat_amp": "batCurrent", "avg_chg_power": "chargingPower", "avg_temp": "temperature",
    "kwh_today": "powerToday", "kwh_total": "powerTotal",
}


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


async def fetch_stats(session: aiohttp.ClientSession, token: str, device_id: str, period: str,
                      start: float, end: float, device_type: str = "MPPT Charger") -> list[dict]:
    params = {"period": period, "type": device_type, "from": _iso(start), "to": _iso(end)}
    async with session.get(
        f"{API}/devices/statistic/{device_id}", params=params,
        headers={**HEADERS, "x-access-token": token}, timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200 or not isinstance(body, dict) or not body.get("success"):
            raise RuntimeError(f"cloud stats HTTP {resp.status}: {str(body)[:120]}")
    return body.get("data") or []


def daily_kwh(rows: list[dict]) -> dict[str, float]:
    """{YYYY-MM-DD: kwh_today} từ các dòng period=1d."""
    return {r["ts"][:10]: float(r["kwh_today"]) for r in rows if r.get("kwh_today") is not None}


def hourly_row(r: dict, device_type) -> tuple[float, dict]:
    """Đổi 1 dòng period=1h của cloud sang (bucket_start, data) cùng dạng rollup local.
    Các trường kWh nhân hệ số phía ắc quy, đánh dấu _scaled để không nhân lần 2."""
    bucket = float(calendar.timegm(time.strptime(r["ts"][:19], "%Y-%m-%dT%H:%M:%S")))
    charger = {v: r[k] for k, v in _FIELD_MAP.items() if r.get(k) is not None}
    for k in ("powerToday", "powerTotal"):
        if k in charger:
            charger[k] = round(charger[k] * BATTERY_SIDE_FACTOR, 4)
    charger["statusText"] = "CHARGING" if r.get("last_status") == 1 else "IDLE"
    return bucket, {"charger": charger, "hasData": True, "_device_type": device_type,
                    "_scaled": BATTERY_SIDE_FACTOR, "_samples": r.get("sample_count")}
