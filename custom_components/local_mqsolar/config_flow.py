import voluptuous as vol
import logging
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
import aiohttp
import asyncio
import socket

from .const import DOMAIN, CONF_MODE, MODE_LOCAL, MODE_CLOUD, CONF_TOKEN, CONF_DEVICES
from .api import MQSolarApiClient

_LOGGER = logging.getLogger(__name__)

async def test_connection(hass, host):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    api = MQSolarApiClient(host, async_get_clientsession(hass))
    try:
        data = await api.fetch_status()
        return data
    except Exception:
        return None

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

async def scan_network(hass):
    ip = get_local_ip()
    if ip == '127.0.0.1':
        return {}
    base_ip = ip.rsplit('.', 1)[0]
    
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    session = async_get_clientsession(hass)
    
    async def check_ip(i):
        target = f"{base_ip}.{i}"
        api = MQSolarApiClient(target, session)
        try:
             import async_timeout
             # Timeout siêu ngắn 1s cho quét nhiều IP
             async with async_timeout.timeout(1):
                 data = await api.fetch_status()
                 if data and "deviceId" in data:
                     return target, data
        except Exception:
             pass
        return None

    tasks = [check_ip(i) for i in range(1, 255)]
    results = await asyncio.gather(*tasks)
    
    return { res[0]: res[1] for res in results if res }

class MQSolarConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(
            step_id="user",
            menu_options=["manual", "scan", "cloud"]
        )

    async def async_step_manual(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input["host"]
            device_info = await test_connection(self.hass, host)
            if device_info:
                await self.async_set_unique_id(device_info["deviceId"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"MQ {device_info.get('device_type', 'Solar')} ({host})", 
                    data={
                        "host": host,
                        CONF_MODE: MODE_LOCAL
                    }
                )
            else:
                errors["base"] = "cannot_connect"
        
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required("host"): str,
            }),
            errors=errors
        )

    async def async_step_scan(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input["host"]
            device_info = await test_connection(self.hass, host)
            if device_info:
                await self.async_set_unique_id(device_info["deviceId"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"MQ {device_info.get('device_type', 'Solar')} ({host})", 
                    data={
                        "host": host,
                        CONF_MODE: MODE_LOCAL
                    }
                )
            else:
                errors["base"] = "cannot_connect"

        # Quét mạng nếu chưa scan
        if not hasattr(self, "_discovered_devices"):
            discovered = await scan_network(self.hass)
            if not discovered:
                return self.async_abort(reason="no_devices_found")
            self._discovered_devices = {}
            for ip, info in discovered.items():
                dtype_raw = info.get('device_type')
                if str(dtype_raw) == "1":
                    dtype = "Inverter"
                else:
                    dtype = "Sạc MPPT"
                self._discovered_devices[ip] = f"{dtype} ({ip})"
        
        return self.async_show_form(
            step_id="scan",
            data_schema=vol.Schema({
                vol.Required("host"): vol.In(self._discovered_devices)
            }),
            errors=errors
        )

    async def async_step_cloud(self, user_input=None):
        errors = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN]
            devices = user_input[CONF_DEVICES]
            # split and clean device list
            device_list = [d.strip() for d in devices.split(",") if d.strip()]
            
            if not device_list:
                errors["base"] = "no_devices_found"
            else:
                # generate unique id based on token (hash it?) or just use first device
                await self.async_set_unique_id(f"cloud_{token[:10]}")
                self._abort_if_unique_id_configured()
                
                return self.async_create_entry(
                    title=f"MQ Solar Cloud ({len(device_list)} devices)",
                    data={
                        CONF_MODE: MODE_CLOUD,
                        CONF_TOKEN: token,
                        CONF_DEVICES: device_list
                    }
                )

        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema({
                vol.Required(CONF_TOKEN): str,
                vol.Required(CONF_DEVICES): str,
            }),
            errors=errors
        )
