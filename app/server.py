from __future__ import annotations

import time
from pathlib import Path

import aiohttp
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .discovery import scan_network
from .poller import Poller
from .storage import Storage

STATIC_DIR = Path(__file__).parent / "static"


def create_app(poller: Poller, storage: Storage) -> FastAPI:
    app = FastAPI(title="MQ Solar Local API", version="1.0.0")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/device/{device_id}", response_class=HTMLResponse)
    async def device_page(device_id: str):
        return FileResponse(STATIC_DIR / "dashboard.html")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/status")
    async def status():
        return poller.latest

    @app.get("/api/status/{device_id}")
    async def status_device(device_id: str):
        data = poller.latest.get(device_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Device not found")
        return data

    @app.get("/api/devices")
    async def devices():
        """Danh sách thiết bị: gộp DB (lịch sử) + RAM (live) — khi tắt lưu
        trữ (storage_enabled=false) DB luôn rỗng nên phải lấy từ poller."""
        result = await storage.known_devices()
        known_ids = {d["device_id"] for d in result}
        now = time.time()
        for device_id, data in poller.latest.items():
            if device_id not in known_ids:
                result.append({
                    "device_id": device_id,
                    "device_type": data.get("_device_type"),
                    "mode": "-",
                    "last_seen": now,
                })
        return result

    @app.get("/api/history/{device_id}")
    async def history(
        device_id: str,
        since: float | None = None,
        until: float | None = None,
        limit: int = Query(500, le=5000),
    ):
        return await storage.get_history(device_id, since=since, until=until, limit=limit)

    @app.get("/api/chart/{device_id}")
    async def chart(device_id: str, range: str = Query("12h", pattern="^(12h|day|month)$")):
        return await storage.get_chart(device_id, range)

    @app.get("/api/scan")
    async def scan():
        """Scan the local /24 subnet for MQ Solar devices (same as the HA config flow)."""
        async with aiohttp.ClientSession() as session:
            return await scan_network(session)

    return app
