"""Constants for the Decast Meter Webhook integration."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume

DOMAIN = "decast"

MANUFACTURER = "Decast"

SIGNAL_NEW_READING = "decast_new_reading_{entry_id}"

RESOURCE_COLD_WATER = "COLD_WATER"
RESOURCE_HOT_WATER = "HOT_WATER"
RESOURCE_HEATING = "HEATING"
RESOURCE_ELECTRICITY = "ELECTRICITY"
RESOURCE_GAS = "GAS"


# Per-resource entity configuration. The Decast payload doesn't carry units,
# so we infer them from the resource enum: water/gas in m³, electricity in
# kWh (the spec confirms this for the tariff fields), heating in Gcal.
RESOURCE_CONFIG: dict[str, dict] = {
    RESOURCE_COLD_WATER: {
        "name": "Cold water",
        "device_class": SensorDeviceClass.WATER,
        "unit": UnitOfVolume.CUBIC_METERS,
        "icon": "mdi:water",
    },
    RESOURCE_HOT_WATER: {
        "name": "Hot water",
        "device_class": SensorDeviceClass.WATER,
        "unit": UnitOfVolume.CUBIC_METERS,
        "icon": "mdi:water-thermometer",
    },
    RESOURCE_ELECTRICITY: {
        "name": "Electricity",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "icon": "mdi:flash",
    },
    RESOURCE_GAS: {
        "name": "Gas",
        "device_class": SensorDeviceClass.GAS,
        "unit": UnitOfVolume.CUBIC_METERS,
        "icon": "mdi:fire",
    },
    RESOURCE_HEATING: {
        "name": "Heating",
        "device_class": None,
        "unit": "Gcal",
        "icon": "mdi:radiator",
    },
}

# Tariff fields on ElectricityReading. We surface them as attributes; users who
# want a per-tariff sensor can build a Template Helper from the attribute.
ELECTRICITY_TARIFF_FIELDS = (
    "tariff1Value",
    "tariff2Value",
    "tariff3Value",
    "tariff4Value",
)
