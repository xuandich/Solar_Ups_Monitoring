from __future__ import annotations

import asyncio
import json
import logging
import time

import aiosqlite

_LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    device_type TEXT,
    mode TEXT NOT NULL,
    ts REAL NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings(device_id, ts);

-- Trung bình mỗi giờ, dùng cho biểu đồ "12 giờ" / "ngày" và làm nguồn cho
-- daily_readings. Giữ được nhiều tháng dữ liệu mà không phình DB như bảng
-- readings thô (poll mỗi 1-2 giây).
CREATE TABLE IF NOT EXISTS hourly_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    device_type TEXT,
    mode TEXT NOT NULL,
    bucket_start REAL NOT NULL,
    data TEXT NOT NULL,
    UNIQUE(device_id, bucket_start)
);
CREATE INDEX IF NOT EXISTS idx_hourly_device_ts ON hourly_readings(device_id, bucket_start);

-- Trung bình mỗi ngày (từ hourly_readings), dùng cho biểu đồ "tháng".
CREATE TABLE IF NOT EXISTS daily_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    device_type TEXT,
    mode TEXT NOT NULL,
    bucket_start REAL NOT NULL,
    data TEXT NOT NULL,
    UNIQUE(device_id, bucket_start)
);
CREATE INDEX IF NOT EXISTS idx_daily_device_ts ON daily_readings(device_id, bucket_start);
"""

HOUR_SECONDS = 3600
DAY_SECONDS = 86400


def _average_payloads(payloads: list[dict]) -> dict | None:
    """Gộp nhiều bản ghi (charger/inverter) thành 1 bản ghi trung bình.

    Các trường số được lấy trung bình cộng, statusText lấy giá trị gần nhất.
    """
    valid = [p for p in payloads if p.get("hasData")]
    if not valid:
        return None

    kind = next((k for k in ("charger", "inverter", "ups") if k in valid[0]), None)
    if not kind:
        return None

    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    last_status = None
    for p in valid:
        sub = p.get(kind) or {}
        for k, v in sub.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                sums[k] = sums.get(k, 0.0) + v
                counts[k] = counts.get(k, 0) + 1
            elif k == "statusText":
                last_status = v

    avg = {k: sums[k] / counts[k] for k in sums}
    if last_status is not None:
        avg["statusText"] = last_status

    return {kind: avg, "hasData": True, "_device_type": valid[0].get("_device_type")}


class Storage:
    """SQLite storage cho readings thô + hourly/daily rollup.

    2 tầng giảm tải độc lập với nhau:
    1. Khối lượng dữ liệu: Poller chỉ gọi insert_reading() tối đa 1 lần mỗi
       raw_persist_interval_seconds/thiết bị (xem app/poller.py) — quyết
       định CÓ BAO NHIÊU mẫu được lưu.
    2. Tần suất ghi đĩa: ở đây, mỗi mẫu nhận được lại được đệm trong RAM và
       chỉ commit (fsync) theo lô mỗi `flush_interval` giây — quyết định
       BAO LÂU MỘT LẦN mới thực sự ghi đĩa. Dừng êm (SIGTERM/./stop.sh) sẽ
       flush hết phần còn đệm trước khi thoát; chỉ mất dữ liệu (tối đa
       `flush_interval` gần nhất) nếu tiến trình bị kill đột ngột.
    """

    def __init__(self, db_path: str, flush_interval: float = 600.0):
        self.db_path = db_path
        self.flush_interval = flush_interval
        self._conn: aiosqlite.Connection | None = None
        self._buffer: list[tuple] = []
        self._flush_task: asyncio.Task | None = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.executescript(_SCHEMA)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.commit()
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def close(self):
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush_buffer()
        if self._conn:
            await self._conn.close()

    async def insert_reading(self, device_id: str, device_type: str | None, mode: str, data: dict, ts: float | None = None):
        ts = ts if ts is not None else time.time()
        self._buffer.append((device_id, device_type, mode, ts, json.dumps(data)))

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(self.flush_interval)
            try:
                await self._flush_buffer()
            except Exception as e:
                _LOGGER.warning("Flush readings thất bại: %s", e)

    async def _flush_buffer(self):
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        await self._conn.executemany(
            "INSERT INTO readings (device_id, device_type, mode, ts, data) VALUES (?, ?, ?, ?, ?)",
            batch,
        )
        await self._conn.commit()

    async def get_history(
        self,
        device_id: str,
        since: float | None = None,
        until: float | None = None,
        limit: int = 500,
    ) -> list[dict]:
        query = "SELECT ts, data FROM readings WHERE device_id = ?"
        params: list = [device_id]
        if since is not None:
            query += " AND ts >= ?"
            params.append(since)
        if until is not None:
            query += " AND ts <= ?"
            params.append(until)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [{"ts": r[0], "data": json.loads(r[1])} for r in rows]

    async def cleanup_old(self, retention_days: int):
        cutoff = time.time() - retention_days * 86400
        await self._conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
        await self._conn.commit()

    async def cleanup_old_charts(self, retention_days: int):
        cutoff = time.time() - retention_days * 86400
        await self._conn.execute("DELETE FROM hourly_readings WHERE bucket_start < ?", (cutoff,))
        await self._conn.execute("DELETE FROM daily_readings WHERE bucket_start < ?", (cutoff,))
        await self._conn.commit()

    async def _rollup_hour(self, device_id: str, device_type: str | None, mode: str, hour_start: float):
        hour_end = hour_start + HOUR_SECONDS
        async with self._conn.execute(
            "SELECT data FROM readings WHERE device_id = ? AND ts >= ? AND ts < ?",
            (device_id, hour_start, hour_end),
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            return

        avg = _average_payloads([json.loads(r[0]) for r in rows])
        if avg is None:
            return

        await self._conn.execute(
            """
            INSERT INTO hourly_readings (device_id, device_type, mode, bucket_start, data)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(device_id, bucket_start) DO UPDATE SET data = excluded.data
            """,
            (device_id, device_type, mode, hour_start, json.dumps(avg)),
        )
        await self._conn.commit()

    async def _rollup_day(self, device_id: str, device_type: str | None, mode: str, day_start: float):
        day_end = day_start + DAY_SECONDS
        async with self._conn.execute(
            "SELECT data FROM hourly_readings WHERE device_id = ? AND bucket_start >= ? AND bucket_start < ?",
            (device_id, day_start, day_end),
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            return

        avg = _average_payloads([json.loads(r[0]) for r in rows])
        if avg is None:
            return

        await self._conn.execute(
            """
            INSERT INTO daily_readings (device_id, device_type, mode, bucket_start, data)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(device_id, bucket_start) DO UPDATE SET data = excluded.data
            """,
            (device_id, device_type, mode, day_start, json.dumps(avg)),
        )
        await self._conn.commit()

    async def run_rollups(self):
        """Tính lại giờ/ngày hiện tại + giờ/ngày trước đó cho mọi thiết bị.

        Gọi định kỳ (vd: mỗi 10 phút). Tính lại cả bucket trước đó để tự
        chốt số liệu (self-healing) nếu service từng bị dừng giữa chừng.
        """
        now = time.time()
        cur_hour = now - (now % HOUR_SECONDS)
        cur_day = now - (now % DAY_SECONDS)

        for d in await self.known_devices():
            device_id, device_type, mode = d["device_id"], d["device_type"], d["mode"]
            for hour_start in (cur_hour - HOUR_SECONDS, cur_hour):
                await self._rollup_hour(device_id, device_type, mode, hour_start)
            for day_start in (cur_day - DAY_SECONDS, cur_day):
                await self._rollup_day(device_id, device_type, mode, day_start)

    async def get_chart(self, device_id: str, range_: str) -> list[dict]:
        """Dữ liệu biểu đồ cột. range_: '12h' | 'day' | 'month'."""
        now = time.time()
        if range_ == "12h":
            table, since, order_limit = "hourly_readings", now - 12 * HOUR_SECONDS, 12
        elif range_ == "day":
            table, since, order_limit = "hourly_readings", now - 24 * HOUR_SECONDS, 24
        elif range_ == "month":
            table, since, order_limit = "daily_readings", now - 31 * DAY_SECONDS, 31
        else:
            raise ValueError(f"range_ không hợp lệ: {range_}")

        query = f"""
            SELECT bucket_start, data FROM {table}
            WHERE device_id = ? AND bucket_start >= ?
            ORDER BY bucket_start ASC LIMIT ?
        """
        async with self._conn.execute(query, (device_id, since, order_limit)) as cursor:
            rows = await cursor.fetchall()
        return [{"ts": r[0], "data": json.loads(r[1])} for r in rows]

    async def known_devices(self) -> list[dict]:
        query = """
            SELECT device_id, device_type, mode, MAX(ts) as last_seen
            FROM readings
            GROUP BY device_id
            ORDER BY last_seen DESC
        """
        async with self._conn.execute(query) as cursor:
            rows = await cursor.fetchall()
        return [
            {"device_id": r[0], "device_type": r[1], "mode": r[2], "last_seen": r[3]}
            for r in rows
        ]
