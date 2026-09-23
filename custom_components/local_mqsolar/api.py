import logging
import asyncio
import async_timeout
import aiohttp
import json

_LOGGER = logging.getLogger(__name__)

def normalize_data(raw_data):
    """Normalize data between local and cloud formats."""
    # If it's cloud data, it has 'payload' and 'deviceId'
    if "payload" in raw_data and "deviceId" in raw_data:
        device_id = raw_data["deviceId"]
        payload = raw_data["payload"]
        topic = raw_data.get("topic", "")
        
        normalized = {
            "_device_id": device_id,
            "hasData": True
        }
        
        # Determine device type from topic or payload
        if "grid_tie_inverter" in topic or "dc_voltage" in payload:
            normalized["_device_type"] = "Inverter"
            normalized["inverter"] = {
                "dcVoltage": payload.get("dc_voltage"),
                "acVoltage": payload.get("ac_voltage"),
                "outputPower": payload.get("output_power"),
                "limiterPower": payload.get("limmiter_power"),
                "limiterToday": payload.get("limmiter_today"),
                "limiterTotal": payload.get("limmiter_total"),
                "temperature": payload.get("temperature"),
                "energyToday": payload.get("energy_today"),
                "energyTotal": payload.get("energy_total"),
                "statusText": "Online"
            }
        elif "mppt_charger" in topic or "pv_voltage" in payload:
            normalized["_device_type"] = "Charger"
            normalized["charger"] = {
                "pvVoltage": payload.get("pv_voltage"),
                "pvCurrent": payload.get("pv_current"),
                "batVoltage": payload.get("bat_voltage"),
                "batCurrent": payload.get("bat_current"),
                "chargingPower": payload.get("charge_power"),
                "powerToday": payload.get("today_kwh"),
                "powerTotal": payload.get("total_kwh"),
                "temperature": payload.get("temperature"),
                "statusText": "Charging" if payload.get("status") == 1 else "Idle"
            }
        return normalized
    
    # Local data already has 'inverter' or 'charger' keys but needs metadata
    if isinstance(raw_data, dict):
        if "inverter" in raw_data or "charger" in raw_data:
            raw_data["hasData"] = True
            return raw_data
        
    return raw_data

class MQSolarApiClient:
    def __init__(self, host, session):
        self.host = host
        self.session = session
        self.device_type = None
        self.device_id = None
        self.endpoint = None

    async def fetch_status(self):
        async with async_timeout.timeout(5):
            async with self.session.get(f"http://{self.host}/api/status") as resp:
                data = await resp.json()
                self.device_type = data.get("device_type", "Unknown")
                self.device_id = data.get("deviceId", "unknown_id")
                return data

    async def fetch_data(self):
        if not self.endpoint:
            try:
                async with async_timeout.timeout(2):
                    async with self.session.get(f"http://{self.host}/api/status") as resp:
                        if resp.status == 200:
                            status_data = await resp.json()
                            self.device_type = status_data.get("device_type", "Unknown")
                            self.device_id = status_data.get("deviceId", "unknown_id")
            except Exception as e:
                _LOGGER.warning("Could not fetch /api/status: %s", e)
            
            try:
                async with async_timeout.timeout(2):
                    async with self.session.get(f"http://{self.host}/api/charger/data") as resp:
                        if resp.status == 200:
                            self.endpoint = "/api/charger/data"
                        else:
                            self.endpoint = "/api/data"
            except Exception:
                self.endpoint = "/api/data"
                    
        async with async_timeout.timeout(5):
            async with self.session.get(f"http://{self.host}{self.endpoint}") as resp:
                data = await resp.json()
                data["_device_type"] = self.device_type 
                data["_device_id"] = self.device_id
                return normalize_data(data)

class MQSolarCloudClient:
    def __init__(self, token, device_ids, session):
        self.token = token
        self.device_ids = device_ids
        self.session = session
        self.data = {} # device_id -> normalized_data
        self._ws = None
        self._listen_task = None
        self._closing = False

    async def connect(self):
        if self._closing:
            return False
            
        url = f"wss://api.manhquansolar.io.vn/ws?token={self.token}"
        try:
            self._ws = await self.session.ws_connect(url, heartbeat=30)
            _LOGGER.info("Connected to MQ Solar Cloud")
            
            # Subscribe
            subscribe_msg = {
                "topic": "subscribe",
                "payload": {
                    "devices": self.device_ids
                }
            }
            await self._ws.send_json(subscribe_msg)
            
            if self._listen_task is None or self._listen_task.done():
                self._listen_task = asyncio.create_task(self._listen())
                
            return True
        except Exception as e:
            _LOGGER.error("Failed to connect to MQ Solar Cloud: %s", e)
            return False

    async def _listen(self):
        while not self._closing:
            if not self._ws or self._ws.closed:
                _LOGGER.info("WebSocket closed, reconnecting...")
                if await self.connect():
                    continue
                else:
                    await asyncio.sleep(10)
                    continue

            try:
                msg = await self._ws.receive(timeout=60)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if "deviceId" in data:
                        normalized = normalize_data(data)
                        self.data[data["deviceId"]] = normalized
                    elif data.get("ok") and "subscribed" in data:
                         _LOGGER.info("Successfully subscribed to devices: %s", data["subscribed"])
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSING):
                    _LOGGER.warning("WebSocket connection event: %s", msg.type)
                    break
            except asyncio.TimeoutError:
                # Heartbeat or timeout
                continue
            except Exception as e:
                _LOGGER.error("WebSocket receive error: %s", e)
                break
        
        if not self._closing:
             _LOGGER.warning("WebSocket listen loop ended, will retry connection")

    async def stop(self):
        self._closing = True
        if self._ws:
            await self._ws.close()
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
