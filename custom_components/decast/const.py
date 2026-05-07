"""Constants for the Decast Meter Webhook integration."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume

DOMAIN = "decast"

MANUFACTURER = "Decast"

SIGNAL_NEW_READING = "decast_new_reading_{entry_id}"

# Fired when the Historical-offset Number entity for a meter changes value,
# so the corresponding sensor can recompute (raw + offset) and write its
# new state. Payload: {"serial": str, "resource": str, "offset": float}.
SIGNAL_OFFSET_CHANGED = "decast_offset_changed_{entry_id}"

# Fired when the Price Number entity changes. The mirror price-Sensor (which
# the HA Energy dashboard picker can see — it filters its entity picker to
# sensor/input_number domains and ignores `number`) listens and updates.
# Payload: {"serial": str, "resource": str, "price": float}.
SIGNAL_PRICE_CHANGED = "decast_price_changed_{entry_id}"

# Fired on the HA event bus for every received webhook (accepted, rejected,
# or ignored) so users can watch live in Developer Tools → Events and
# trigger automations from incoming readings.
EVENT_WEBHOOK_RECEIVED = "decast_webhook_received"

# Size of the in-memory ring buffer used by the diagnostics handler.
WEBHOOK_LOG_MAX = 50

# Key in `hass.data[DOMAIN][entry_id]` for the shared meter-state dict that
# the sensor and number platforms both read/write. Maps (serial, resource)
# tuples to {"raw_value": float|None, "offset": float, "price": float}.
DATA_METERS = "meters"

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
        "key": "cold_water",
        "device_class": SensorDeviceClass.WATER,
        "unit": UnitOfVolume.CUBIC_METERS,
        "icon": "mdi:water",
    },
    RESOURCE_HOT_WATER: {
        "key": "hot_water",
        "device_class": SensorDeviceClass.WATER,
        "unit": UnitOfVolume.CUBIC_METERS,
        "icon": "mdi:water-thermometer",
    },
    RESOURCE_ELECTRICITY: {
        "key": "electricity",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "icon": "mdi:flash",
    },
    RESOURCE_GAS: {
        "key": "gas",
        "device_class": SensorDeviceClass.GAS,
        "unit": UnitOfVolume.CUBIC_METERS,
        "icon": "mdi:fire",
    },
    RESOURCE_HEATING: {
        "key": "heating",
        "device_class": None,
        "unit": "Gcal",
        "icon": "mdi:radiator",
    },
}


def price_unit(currency: str, consumption_unit: str) -> str:
    """Build the unit-of-measurement for a price entity.

    HA's Energy dashboard validates that the price entity's unit is
    `<currency-iso-code>/<consumption-unit>` (e.g. ``RUB/m³``). We compose
    it from `hass.config.currency` so it stays consistent with whatever
    locale HA is configured for.
    """
    return f"{currency or 'EUR'}/{consumption_unit}"

# Tariff fields on ElectricityReading. We surface them as attributes; users who
# want a per-tariff sensor can build a Template Helper from the attribute.
ELECTRICITY_TARIFF_FIELDS = (
    "tariff1Value",
    "tariff2Value",
    "tariff3Value",
    "tariff4Value",
)
