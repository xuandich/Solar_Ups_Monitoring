"""Đọc bản sao lưu thống kê MPPT trên Google Drive qua web app Google Apps Script
(google_apps_script/mqsolar_backup.gs, hàm doGet). Dòng dữ liệu có dạng y hệt
API thống kê của cloud nên dùng lại hàm xử lý trong cloud_sync.
"""
from __future__ import annotations

import asyncio
import json

import aiohttp

from .cloud_sync import _iso


ATTEMPTS = 3
RETRY_DELAYS = (5, 15)   # giây chờ trước lần thử 2 và 3
ATTEMPT_TIMEOUT = 70     # web app Apps Script khởi động nguội có thể mất ~1 phút rồi trả lỗi 404


async def fetch_backup(session: aiohttp.ClientSession, url: str, key: str, device_id: str,
                       since: float | None = None) -> dict:
    params = {"key": key, "device": device_id}
    if since is not None:
        params["since"] = _iso(since)
    last_err = ""
    for attempt in range(ATTEMPTS):
        # Web app Apps Script đôi khi trả trang lỗi tạm thời (404 "không thể mở tệp"), thường sau
        # một lần khởi động nguội, và lần gọi kế tiếp chạy bình thường -> thử lại vài lần.
        # Web app trả 302 sang script.googleusercontent.com; aiohttp tự theo.
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=ATTEMPT_TIMEOUT)) as resp:
                text = await resp.text()
                try:
                    body = json.loads(text)
                except ValueError:
                    body = None
                if resp.status == 200 and isinstance(body, dict) and body.get("ok"):
                    return body
                if isinstance(body, dict):   # JSON hợp lệ nhưng ok=false (vd sai mã) -> không thử lại
                    raise RuntimeError(f"Google Drive từ chối: {body.get('error', body)}")
                last_err = (f"Google Drive tạm thời không phản hồi đúng (HTTP {resp.status}; thường do web app "
                            "Apps Script khởi động chậm, hoặc chưa cho 'Bất kỳ ai' truy cập)")
        except asyncio.TimeoutError:
            last_err = f"Google Drive không phản hồi trong {ATTEMPT_TIMEOUT} giây"
        except aiohttp.ClientError as e:
            last_err = f"không kết nối được Google Drive: {e}"
        if attempt < ATTEMPTS - 1:
            await asyncio.sleep(RETRY_DELAYS[attempt])
    raise RuntimeError(last_err + f" (đã thử {ATTEMPTS} lần)")
