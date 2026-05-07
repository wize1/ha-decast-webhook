"""Sensor platform for Decast meter readings."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    ELECTRICITY_TARIFF_FIELDS,
    MANUFACTURER,
    RESOURCE_CONFIG,
    RESOURCE_ELECTRICITY,
    SIGNAL_NEW_READING,
)

_LOGGER = logging.getLogger(__name__)

# Unique-id format: "{serial}::{resource}". Serial numbers are globally
# unique to the physical meter, so this stays stable across integration
# remove/re-add and preserves long-term statistics.
_UID_SEP = "::"


def _make_unique_id(serial: str, resource: str) -> str:
    return f"{serial}{_UID_SEP}{resource}"


def _parse_unique_id(uid: str) -> tuple[str, str] | None:
    parts = uid.split(_UID_SEP)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Restore previously-known meters and subscribe to new readings."""
    sensors: dict[str, DecastReadingSensor] = {}

    # Recreate sensors for meters HA has seen before so they appear immediately
    # on restart instead of waiting for the next webhook.
    ent_reg = er.async_get(hass)
    initial: list[DecastReadingSensor] = []
    for ent_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent_entry.domain != "sensor":
            continue
        parsed = _parse_unique_id(ent_entry.unique_id)
        if parsed is None:
            continue
        serial, resource = parsed
        if resource not in RESOURCE_CONFIG:
            continue
        sensor = DecastReadingSensor(serial, resource)
        sensors[ent_entry.unique_id] = sensor
        initial.append(sensor)

    if initial:
        async_add_entities(initial)

    @callback
    def _handle_reading(data: dict[str, Any]) -> None:
        serial = data["serial"]
        resource = data["resource"]
        if resource not in RESOURCE_CONFIG:
            _LOGGER.warning("Unsupported Decast resource: %s", resource)
            return

        uid = _make_unique_id(serial, resource)
        existing = sensors.get(uid)
        if existing is not None:
            existing.update_from_data(data)
            return

        sensor = DecastReadingSensor(serial, resource, data)
        sensors[uid] = sensor
        async_add_entities([sensor])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_NEW_READING.format(entry_id=entry.entry_id),
            _handle_reading,
        )
    )


class DecastReadingSensor(SensorEntity, RestoreEntity):
    """A single (meter, resource) reading sensor."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        serial: str,
        resource: str,
        initial_data: dict[str, Any] | None = None,
    ) -> None:
        cfg = RESOURCE_CONFIG[resource]
        self._serial = serial
        self._resource = resource
        self._reading_time: datetime | None = None
        self._utility: dict[str, Any] | None = None
        self._raw_reading: dict[str, Any] | None = None

        self._attr_unique_id = _make_unique_id(serial, resource)
        self._attr_name = cfg["name"]
        self._attr_native_unit_of_measurement = cfg["unit"]
        self._attr_device_class = cfg["device_class"]
        self._attr_icon = cfg["icon"]
        self._attr_native_value = None

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Decast meter {serial}",
            manufacturer=MANUFACTURER,
            model="IoT meter",
            serial_number=serial,
        )

        if initial_data is not None:
            self._apply(initial_data)

    def _apply(self, data: dict[str, Any]) -> None:
        self._attr_native_value = data["value"]
        self._reading_time = data.get("reading_time")
        self._utility = data.get("utility")
        self._raw_reading = data.get("raw_reading")

    @callback
    def update_from_data(self, data: dict[str, Any]) -> None:
        self._apply(data)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._attr_native_value is not None:
            return

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in (None, "", "unknown", "unavailable"):
            return
        try:
            self._attr_native_value = float(last_state.state)
        except (TypeError, ValueError):
            return

        attrs = last_state.attributes
        if (raw_time := attrs.get("reading_time")) is not None:
            try:
                self._reading_time = datetime.fromisoformat(raw_time)
            except (TypeError, ValueError):
                self._reading_time = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "serial_number": self._serial,
            "resource": self._resource,
        }
        if self._reading_time is not None:
            attrs["reading_time"] = self._reading_time.isoformat()

        utility = self._utility or {}
        if loc := (utility.get("location") or {}).get("fullValue"):
            attrs["location"] = loc
        if place := utility.get("place"):
            attrs["place"] = place
        if ca := utility.get("contractAccount"):
            attrs["contract_account"] = ca
        if tz := utility.get("timezone"):
            attrs["timezone"] = tz

        if self._resource == RESOURCE_ELECTRICITY and self._raw_reading:
            for field in ELECTRICITY_TARIFF_FIELDS:
                if (val := self._raw_reading.get(field)) is not None:
                    attrs[field] = val

        return attrs
