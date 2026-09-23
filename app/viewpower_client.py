"""Client cho server ViewPower (phần mềm giám sát UPS, thường dùng cho UPS Vertiv/Liebert).

ViewPower chạy trên Tomcat, trang giám sát tự poll JSON qua:
    POST /ViewPower/workstatus/reqMonitorData
    body: portName=<port name, vd: USBusbdev1>

Không cần đăng nhập cho endpoint này (đã test trực tiếp). portName lấy được
bằng cách mở trang .../ViewPower/monitor rồi xem giá trị mặc định trong
window.sessionStorage (thường là "USBusbdev1" cho UPS nối qua USB).
"""

from __future__ import annotations

import json
import logging

import async_timeout
import aiohttp

_LOGGER = logging.getLogger(__name__)


class ViewPowerError(Exception):
    pass


async def fetch_work_info(host: str, port: int, port_name: str, session: aiohttp.ClientSession, timeout: float = 5.0) -> dict:
    """POST reqMonitorData, trả về dict workInfo thô (mọi field ViewPower báo)."""
    url = f"http://{host}:{port}/ViewPower/workstatus/reqMonitorData"
    async with async_timeout.timeout(timeout):
        async with session.post(url, data={"portName": port_name}) as resp:
            text = await resp.text()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ViewPowerError(f"Phản hồi ViewPower không phải JSON hợp lệ: {e}") from e

    work_info = payload.get("workInfo")
    if not work_info:
        raise ViewPowerError("Phản hồi ViewPower thiếu workInfo (kiểm tra lại portName)")
    return work_info


def _num(work_info: dict, key: str) -> float | None:
    v = work_info.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_viewpower(work_info: dict, nominal_watts: float | None = None) -> dict:
    """Rút gọn workInfo thô của ViewPower thành cấu trúc 'ups' dùng chung app."""
    load_pct = _num(work_info, "outputLoadPercent")
    load_watts = (load_pct / 100 * nominal_watts) if (load_pct is not None and nominal_watts is not None) else None

    # dischargeCurr đơn lẻ không đáng tin cho mọi driver (thực tế đo được:
    # báo 0 dù workMode đã là "Battery mode" và inputVoltage=0V khi mất điện
    # lưới thật) — kết hợp thêm 2 tín hiệu trực tiếp hơn: điện áp đầu vào
    # gần 0 (không còn AC) và chữ "battery" ngay trong workMode.
    input_voltage = _num(work_info, "inputVoltage")
    work_mode_raw = (work_info.get("workMode") or "").lower()
    discharge_curr = _num(work_info, "dischargeCurr") or 0
    on_battery = (
        (input_voltage is not None and input_voltage < 50)
        or ("battery" in work_mode_raw)
        or (discharge_curr > 0)
    )

    return {
        "ups": {
            "batteryCharge": _num(work_info, "batteryCapacity"),
            "batteryRuntimeMin": _num(work_info, "batteryRemainTime"),
            "batteryVoltage": _num(work_info, "batteryVoltage"),
            "inputVoltage": _num(work_info, "inputVoltage"),
            "inputFrequency": _num(work_info, "inputFrequency"),
            "outputVoltage": _num(work_info, "outputVoltage"),
            "outputFrequency": _num(work_info, "outputFrequency"),
            "outputCurrent": _num(work_info, "outputCurrent"),
            "load": load_pct,
            "loadWatts": load_watts,
            "nominalWatts": nominal_watts,
            "temperature": _num(work_info, "temperatureView") or _num(work_info, "temperature"),
            "workMode": work_info.get("workMode") or "",
            "bypassActive": bool(work_info.get("bypassActive")),
            "onBattery": on_battery,
            "morphological": work_info.get("morphological") or "",
            "statusText": work_info.get("workMode") or "UNKNOWN",
        },
        "hasData": True,
        "_device_type": "UPS",
    }
