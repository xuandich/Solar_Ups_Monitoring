from __future__ import annotations

import asyncio
import socket

import async_timeout
import aiohttp

from .mqsolar_client import MQSolarApiClient


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


async def scan_network(session: aiohttp.ClientSession) -> dict:
    """Scan our /24 subnet for MQ Solar local devices."""
    ip = get_local_ip()
    if ip == "127.0.0.1":
        return {}
    base_ip = ip.rsplit(".", 1)[0]

    async def check_ip(i: int):
        target = f"{base_ip}.{i}"
        api = MQSolarApiClient(target, session)
        try:
            async with async_timeout.timeout(1):
                data = await api.fetch_status()
                if data and "deviceId" in data:
                    return target, data
        except Exception:
            pass
        return None

    tasks = [check_ip(i) for i in range(1, 255)]
    results = await asyncio.gather(*tasks)
    return {res[0]: res[1] for res in results if res}
