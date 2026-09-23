import logging
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTemperature,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, MODE_CLOUD

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    added_devices = set()

    def get_entities_for_device(device_data, device_id):
        entities = []
        device_type = device_data.get("_device_type", "MQSolar")
        
        # Thêm hậu tố Cloud vào tên nếu là kết nối cloud
        mode_label = "Cloud" if coordinator.mode == MODE_CLOUD else "Local"
        
        _LOGGER.info("Creating %s entities for device %s (%s)", mode_label, device_id, device_type)
        
        device_info = {
            # Sử dụng mode trong identifiers để tránh trùng lặp thiết bị giữa Local và Cloud
            "identifiers": {(DOMAIN, f"{device_id}_{coordinator.mode}")},
            "name": f"MQ {device_type} {device_id} ({mode_label})",
            "manufacturer": "Mạnh Quân",
            "model": device_type,
        }
        
        d_id = device_id if coordinator.mode == MODE_CLOUD else None

        if "charger" in device_data:
            entities.extend([
                MQSolarSensor(coordinator, device_info, "pvVoltage", "PV Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, "charger", d_id),
                MQSolarSensor(coordinator, device_info, "pvCurrent", "PV Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, "charger", d_id),
                MQSolarSensor(coordinator, device_info, "batVoltage", "Battery Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, "charger", d_id),
                MQSolarSensor(coordinator, device_info, "batCurrent", "Battery Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, "charger", d_id),
                MQSolarSensor(coordinator, device_info, "chargingPower", "Charging Power", UnitOfPower.WATT, SensorDeviceClass.POWER, "charger", d_id),
                MQSolarSensor(coordinator, device_info, "powerToday", "Energy Today", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, "charger", d_id, SensorStateClass.TOTAL_INCREASING),
                MQSolarSensor(coordinator, device_info, "powerTotal", "Energy Total", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, "charger", d_id, SensorStateClass.TOTAL),
                MQSolarSensor(coordinator, device_info, "temperature", "Temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "charger", d_id),
                MQSolarTextSensor(coordinator, device_info, "statusText", "Status", "charger", d_id),
            ])
        elif "inverter" in device_data:
            entities.extend([
                MQSolarSensor(coordinator, device_info, "dcVoltage", "DC Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, "inverter", d_id),
                MQSolarSensor(coordinator, device_info, "acVoltage", "AC Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, "inverter", d_id),
                MQSolarSensor(coordinator, device_info, "outputPower", "Output Power", UnitOfPower.WATT, SensorDeviceClass.POWER, "inverter", d_id),
                MQSolarSensor(coordinator, device_info, "limiterPower", "Grid Power", UnitOfPower.WATT, SensorDeviceClass.POWER, "inverter", d_id),
                MQSolarSensor(coordinator, device_info, "limiterToday", "Grid Today", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, "inverter", d_id, SensorStateClass.TOTAL_INCREASING),
                MQSolarSensor(coordinator, device_info, "limiterTotal", "Grid Total", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, "inverter", d_id, SensorStateClass.TOTAL),
                MQSolarSensor(coordinator, device_info, "temperature", "Temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "inverter", d_id),
                MQSolarSensor(coordinator, device_info, "energyToday", "Energy Today", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, "inverter", d_id, SensorStateClass.TOTAL_INCREASING),
                MQSolarSensor(coordinator, device_info, "energyTotal", "Energy Total", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, "inverter", d_id, SensorStateClass.TOTAL),
                MQSolarTextSensor(coordinator, device_info, "statusText", "Status", "inverter", d_id),
            ])
        return entities

    def update_entities():
        """Check for new devices and add them."""
        new_entities = []
        
        if coordinator.mode == MODE_CLOUD:
            if not coordinator.data:
                return
                
            for dev_id, dev_data in coordinator.data.items():
                if dev_id not in added_devices:
                    new_entities.extend(get_entities_for_device(dev_data, dev_id))
                    added_devices.add(dev_id)
        else:
            if coordinator.data and "local" not in added_devices:
                device_id = coordinator.data.get("_device_id", "unknown")
                new_entities.extend(get_entities_for_device(coordinator.data, device_id))
                added_devices.add("local")
                
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(update_entities))
    update_entities()

class MQSolarSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, device_info, key, name, unit, device_class, data_key, device_id=None, state_class=SensorStateClass.MEASUREMENT):
        super().__init__(coordinator)
        self._device_info = device_info
        self._key = key
        self._data_key = data_key
        self._device_id = device_id
        
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        
        base_id = list(device_info['identifiers'])[0][1]
        self._attr_unique_id = f"{base_id}_{key}"

    @property
    def device_info(self):
        return self._device_info

    @property
    def native_value(self):
        data = self.coordinator.data
        if self._device_id:
            data = data.get(self._device_id)
            
        if data and data.get("hasData"):
            return data.get(self._data_key, {}).get(self._key)
        return None

class MQSolarTextSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, device_info, key, name, data_key, device_id=None):
        super().__init__(coordinator)
        self._device_info = device_info
        self._key = key
        self._data_key = data_key
        self._device_id = device_id
        
        self._attr_name = name
        
        base_id = list(device_info['identifiers'])[0][1]
        self._attr_unique_id = f"{base_id}_{key}"

    @property
    def device_info(self):
        return self._device_info

    @property
    def native_value(self):
        data = self.coordinator.data
        if self._device_id:
            data = data.get(self._device_id)
            
        if data and data.get("hasData"):
            return data.get(self._data_key, {}).get(self._key)
        return None
