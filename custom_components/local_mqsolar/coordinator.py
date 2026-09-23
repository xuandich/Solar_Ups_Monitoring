from datetime import timedelta
import logging
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import MQSolarApiClient, MQSolarCloudClient
from .const import MODE_CLOUD

_LOGGER = logging.getLogger(__name__)

class MQSolarCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, mode, **kwargs):
        super().__init__(
            hass,
            _LOGGER,
            name="Mạnh Quân Solar Data",
            update_interval=timedelta(seconds=1),  # Update mỗi 1 giây
        )
        self.mode = mode
        session = async_get_clientsession(hass)
        
        if mode == MODE_CLOUD:
            token = kwargs.get("token")
            device_ids = kwargs.get("device_ids")
            self.api = MQSolarCloudClient(token, device_ids, session)
        else:
            host = kwargs.get("host")
            self.api = MQSolarApiClient(host, session)

    async def _async_update_data(self):
        try:
            if self.mode == MODE_CLOUD:
                # Nếu chưa connect thì thử connect
                if not self.api._ws or self.api._ws.closed:
                    await self.api.connect()
                # Trả về toàn bộ data các thiết bị (trả về copy để HA nhận biết sự thay đổi)
                return dict(self.api.data)
            else:
                return await self.api.fetch_data()
        except Exception as err:
            raise UpdateFailed(f"Update failed: {err}")

    async def async_stop(self):
        """Dừng kết nối nếu là cloud."""
        if self.mode == MODE_CLOUD:
            await self.api.stop()
