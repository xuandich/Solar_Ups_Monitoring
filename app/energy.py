"""Điện năng THỰC vào ắc quy của MPPT, tích phân từ batVoltage × batCurrent.

Bộ đếm kWh của thiết bị (powerToday/powerTotal, cũng là kwh_today/kwh_total
trên cloud) tính theo công suất PV nên cao hơn ~9% so với điện vào ắc quy
(hệ số đo thực: BATTERY_SIDE_FACTOR). Mọi số kWh lấy từ thiết bị/cloud phải
nhân hệ số này trước khi cộng chung với số tự tích phân.

Dữ liệu lưu theo từng NGÀY (giờ địa phương), tổng hôm nay/tháng/năm/tích luỹ
là tổng các ngày. Mỗi ngày có:
  live  - Wh tự tích phân khi service chạy
  gap   - Wh vá cho quãng service tạm dừng TRONG CÙNG ngày, tính từ mức tăng
          của bộ đếm thiết bị (thiết bị vẫn đếm khi máy tắt) × hệ số
  cloud - Wh ước tính từ cloud (kwh_today × hệ số), do đồng bộ định kỳ ghi vào
  flag  - ngày có quãng tạm dừng kéo dài nhiều ngày (hoặc không có dữ liệu
          local): lấy max(live + gap, cloud) thay vì chỉ live + gap
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

BATTERY_SIDE_FACTOR = 0.91   # đo 2026-10-02: ắc quy 30.27 Wh / PV 33.27 Wh
MAX_GAP = 30.0               # khoảng cách mẫu lớn hơn mức này = service đã tạm dừng
SAVE_INTERVAL = 30.0


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _days_between(ts_a: float, ts_b: float) -> list[str]:
    days, t = [], ts_a
    while _day(t) <= _day(ts_b):
        days.append(_day(t))
        t += 86400
    if days[-1] != _day(ts_b):
        days.append(_day(ts_b))
    return days


def _effective(d: dict) -> float:
    measured = d.get("live", 0.0) + d.get("gap", 0.0)
    if d.get("flag"):
        return max(measured, d.get("cloud") or 0.0)
    return measured


class EnergyTracker:
    def __init__(self, path: Path):
        self.path = path
        self.state: dict[str, dict] = self._load()
        self._saved = 0.0

    def _load(self) -> dict:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in state.items() if isinstance(v, dict) and "days" in v}

    def save(self):
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=1))
            tmp.replace(self.path)
            self._saved = time.time()
        except OSError as e:
            _LOGGER.warning("Could not save energy counters: %s", e)

    def _entry(self, device_id: str) -> dict:
        return self.state.setdefault(device_id, {"days": {}})

    @staticmethod
    def _new_day() -> dict:
        return {"live": 0.0, "gap": 0.0, "cloud": None, "flag": False}

    def update(self, device_id: str, charger: dict, now: float | None = None) -> dict[str, float] | None:
        """Gọi mỗi lần poll. Trả về kWh {today, month, year, total} hoặc None."""
        v, i = charger.get("batVoltage"), charger.get("batCurrent")
        if v is None or i is None:
            return None
        now = now if now is not None else time.time()
        power = v * max(0.0, i)
        dev_total = charger.get("powerTotal")
        e = self._entry(device_id)
        days = e["days"]
        today = days.setdefault(_day(now), self._new_day())

        last_ts = e.get("ts")
        if last_ts is not None and 0 < now - last_ts <= MAX_GAP:
            today["live"] += (e.get("power", power) + power) / 2 * (now - last_ts) / 3600
        elif last_ts is not None and now - last_ts > MAX_GAP:
            self._handle_pause(e, last_ts, now, dev_total)

        e["ts"], e["power"] = now, power
        if dev_total is not None:
            e["dev_total"] = dev_total

        if now - self._saved >= SAVE_INTERVAL:
            self.save()
        return self.totals(device_id, now)

    def _handle_pause(self, e: dict, last_ts: float, now: float, dev_total: float | None):
        """Service vừa chạy lại sau một quãng tạm dừng (máy tắt, restart...)."""
        days = e["days"]
        span = _days_between(last_ts, now)
        prev_dev = e.get("dev_total")
        if len(span) == 1 and prev_dev is not None and dev_total is not None:
            # Cùng 1 ngày: bộ đếm thiết bị tăng bao nhiêu trong lúc tạm dừng thì
            # đó chính là điện bị bỏ lỡ (chính xác, không cần cloud).
            missing = max(0.0, dev_total - prev_dev) * 1000 * BATTERY_SIDE_FACTOR
            days.setdefault(span[0], self._new_day())["gap"] += missing
            _LOGGER.info("Vá %.1f Wh bị bỏ lỡ trong lúc tạm dừng (%s)", missing, span[0])
        else:
            for d in span:
                days.setdefault(d, self._new_day())["flag"] = True
            _LOGGER.info("Tạm dừng kéo dài nhiều ngày %s..%s: chờ đồng bộ cloud để vá", span[0], span[-1])

    def totals(self, device_id: str, now: float | None = None) -> dict[str, float]:
        now = now if now is not None else time.time()
        today = _day(now)
        out = {"today": 0.0, "month": 0.0, "year": 0.0, "total": 0.0}
        for day, d in self.state.get(device_id, {}).get("days", {}).items():
            wh = _effective(d)
            out["total"] += wh
            if day[:4] == today[:4]:
                out["year"] += wh
            if day[:7] == today[:7]:
                out["month"] += wh
            if day == today:
                out["today"] += wh
        return {k: round(v / 1000, 3) for k, v in out.items()}

    def apply_cloud_days(self, device_id: str, daily_kwh: dict[str, float]):
        """daily_kwh: {YYYY-MM-DD: kwh_today theo cloud (phía PV)} → Wh phía ắc quy."""
        days = self._entry(device_id)["days"]
        for day, kwh in daily_kwh.items():
            if day not in days:
                d = days[day] = self._new_day()
                d["flag"] = True   # không có số đo local nào cho ngày này
            days[day]["cloud"] = kwh * 1000 * BATTERY_SIDE_FACTOR
        self.save()

    def known_days(self, device_id: str) -> set[str]:
        return set(self.state.get(device_id, {}).get("days", {}))
