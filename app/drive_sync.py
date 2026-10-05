"""Đọc bản sao lưu thống kê MPPT trên Google Drive qua web app Google Apps Script
(google_apps_script/mqsolar_backup.gs, hàm doGet). Dòng dữ liệu có dạng y hệt
API thống kê của cloud nên dùng lại hàm xử lý trong cloud_sync.
"""
from __future__ import annotations

import asyncio
import json

import aiohttp

from .cloud_sync import _iso


async def fetch_backup(session: aiohttp.ClientSession, url: str, key: str, device_id: str,
                       since: float | None = None) -> dict:
    params = {"key": key, "device": device_id}
    if since is not None:
        params["since"] = _iso(since)
    last_err = ""
    for attempt in range(2):   # web app Apps Script đôi khi trả trang lỗi tạm thời -> thử lại 1 lần
        # web app trả 302 sang script.googleusercontent.com; aiohttp tự theo.
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=90)) as resp:
            text = await resp.text()
            try:
                body = json.loads(text)
            except ValueError:
                body = None
            if resp.status == 200 and isinstance(body, dict) and body.get("ok"):
                return body
            if isinstance(body, dict):   # JSON hợp lệ nhưng ok=false (vd sai mã) -> không thử lại
                raise RuntimeError(f"Google Drive từ chối: {body.get('error', body)}")
            snippet = " ".join(text.split())[:80]
            last_err = (f"Google không trả JSON (HTTP {resp.status}: {snippet!r}); có thể web app chưa cho "
                        "'Bất kỳ ai' truy cập, đang quá tải hoặc lỗi tạm thời")
        if attempt == 0:
            await asyncio.sleep(3)
    raise RuntimeError(last_err)
