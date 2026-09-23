from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from .const import DOMAIN, CONF_MODE, MODE_CLOUD, CONF_TOKEN, CONF_DEVICES
from .coordinator import MQSolarCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Thiết lập MQ Solar từ config base."""
    hass.data.setdefault(DOMAIN, {})
    
    mode = entry.data.get(CONF_MODE, "local") # mặc định local nếu cũ
    
    if mode == MODE_CLOUD:
        coordinator = MQSolarCoordinator(
            hass, 
            mode, 
            token=entry.data.get(CONF_TOKEN),
            device_ids=entry.data.get(CONF_DEVICES)
        )
    else:
        coordinator = MQSolarCoordinator(
            hass, 
            mode, 
            host=entry.data.get("host")
        )
        
    await coordinator.async_config_entry_first_refresh()
    
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Hủy thiết lập MQ Solar."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.async_stop()
        
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
