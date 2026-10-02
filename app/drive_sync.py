"""Đọc bản sao lưu thống kê MPPT trên Google Drive qua web app Google Apps Script
(google_apps_script/mqsolar_backup.gs, hàm doGet). Dòng dữ liệu có dạng y hệt
API thống kê của cloud nên dùng lại hàm xử lý trong cloud_sync.
"""
from __future__ import annotations

import aiohttp

from .cloud_sync import _iso


async def fetch_backup(session: aiohttp.ClientSession, url: str, key: str, device_id: str,
                       since: float | None = None) -> dict:
    params = {"key": key, "device": device_id}
    if since is not None:
        params["since"] = _iso(since)
    # web app Apps Script trả 302 sang script.googleusercontent.com; aiohttp tự theo.
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=90)) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200 or not isinstance(body, dict) or not body.get("ok"):
            raise RuntimeError(f"drive backup HTTP {resp.status}: {str(body)[:120]}")
    return body
