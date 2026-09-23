import asyncio
import logging
import signal
import socket
import sys

import uvicorn

from app.config import load_config
from app.poller import Poller
from app.server import create_app
from app.storage import Storage

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
_LOGGER = logging.getLogger("mqsolar")


def _port_available(host: str, port: int) -> bool:
    """Thử bind thật để biết trước cổng có rảnh không.

    uvicorn tự sys.exit() khi bind thất bại, việc này làm sập toàn bộ event
    loop ngay lập tức (không có cách bắt lại từ coroutine khác) nên phải kiểm
    tra trước khi khởi tạo storage/poller — nếu không, thread nền của
    aiosqlite (không phải daemon) sẽ giữ tiến trình treo vô thời hạn.
    """
    bind_host = "0.0.0.0" if host in ("0.0.0.0", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # asyncio.loop.create_server() bật SO_REUSEADDR theo mặc định trên
        # POSIX, nên phải bật giống vậy ở đây — nếu không, cổng vừa được giải
        # phóng (còn ở trạng thái TIME_WAIT) sẽ bị báo nhầm là đang bận dù
        # uvicorn vẫn bind bình thường.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((bind_host, port))
            return True
        except OSError:
            return False


async def run():
    config = load_config()

    if not _port_available(config.server_host, config.server_port):
        _LOGGER.error(
            "Cổng %s đã có tiến trình khác sử dụng, không thể khởi động. "
            "Kiểm tra bằng: ss -ltnp | grep %s",
            config.server_port,
            config.server_port,
        )
        return 1

    storage = Storage(config.db_path, flush_interval=config.db_flush_interval_seconds)
    await storage.connect()

    poller = Poller(config, storage)
    await poller.start()

    app = create_app(poller, storage)
    server = uvicorn.Server(
        uvicorn.Config(app, host=config.server_host, port=config.server_port, log_level="info")
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    server_task = asyncio.create_task(server.serve())

    await stop_event.wait()
    _LOGGER.info("Shutting down...")
    server.should_exit = True
    await server_task

    await poller.stop()
    await storage.close()
    return 0


def main():
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
