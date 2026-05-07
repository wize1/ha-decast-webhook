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

from . import get_meter_state
from .const import (
    DOMAIN,
    ELECTRICITY_TARIFF_FIELDS,
    MANUFACTURER,
    RESOURCE_CONFIG,
    RESOURCE_ELECTRICITY,
    SIGNAL_NEW_READING,
    SIGNAL_OFFSET_CHANGED,
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
        sensor = DecastReadingSensor(entry.entry_id, serial, resource)
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

        sensor = DecastReadingSensor(entry.entry_id, serial, resource, data)
        sensors[uid] = sensor
        async_add_entities([sensor])

    @callback
    def _handle_offset_changed(data: dict[str, Any]) -> None:
        uid = _make_unique_id(data["serial"], data["resource"])
        if (sensor := sensors.get(uid)) is not None:
            sensor.refresh_from_state()

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_NEW_READING.format(entry_id=entry.entry_id),
            _handle_reading,
        )
    )
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_OFFSET_CHANGED.format(entry_id=entry.entry_id),
            _handle_offset_changed,
        )
    )


class DecastReadingSensor(SensorEntity, RestoreEntity):
    """A single (meter, resource) reading sensor."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry_id: str,
        serial: str,
        resource: str,
        initial_data: dict[str, Any] | None = None,
    ) -> None:
        cfg = RESOURCE_CONFIG[resource]
        self._entry_id = entry_id
        self._serial = serial
        self._resource = resource
        self._reading_time: datetime | None = None
        self._utility: dict[str, Any] | None = None
        self._raw_reading: dict[str, Any] | None = None

        self._attr_unique_id = _make_unique_id(serial, resource)
        self._attr_translation_key = cfg["key"]
        self._attr_native_unit_of_measurement = cfg["unit"]
        self._attr_device_class = cfg["device_class"]
        self._attr_icon = cfg["icon"]
        self._attr_native_value = None

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            translation_key="meter",
            translation_placeholders={"serial": serial},
            manufacturer=MANUFACTURER,
            model="IoT meter",
            serial_number=serial,
        )

        if initial_data is not None:
            self._apply(initial_data)

    def _meter(self) -> dict[str, Any] | None:
        # `self.hass` is None until the entity is added — guard so __init__
        # and other pre-add code paths don't blow up.
        if self.hass is None:
            return None
        return get_meter_state(
            self.hass, self._entry_id, self._serial, self._resource
        )

    def _compute_native_value(self) -> float | None:
        meter = self._meter()
        if meter is None:
            return None
        raw = meter.get("raw_value")
        if raw is None:
            return None
        return float(raw) + float(meter.get("offset", 0.0))

    def _apply(self, data: dict[str, Any]) -> None:
        # Keep metadata fresh from each webhook. native_value is recomputed
        # from shared state if we're attached to hass; otherwise leave it for
        # async_added_to_hass to fill in.
        self._reading_time = data.get("reading_time")
        self._utility = data.get("utility")
        self._raw_reading = data.get("raw_reading")
        if self.hass is not None:
            self._attr_native_value = self._compute_native_value()

    @callback
    def update_from_data(self, data: dict[str, Any]) -> None:
        self._apply(data)
        self.async_write_ha_state()

    @callback
    def refresh_from_state(self) -> None:
        """Recompute native_value from shared state (offset changed)."""
        self._attr_native_value = self._compute_native_value()
        # Only write if we're actually attached — async_added_to_hass may not
        # have run yet during initial setup.
        if self.hass is not None and self.entity_id is not None:
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # If the offset Number entity already wrote into shared state during
        # its own added_to_hass, prefer the freshly-computed value.
        if (computed := self._compute_native_value()) is not None:
            self._attr_native_value = computed
            return

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
        meter = self._meter() or {}
        attrs: dict[str, Any] = {
            "serial_number": self._serial,
            "resource": self._resource,
            "raw_value": meter.get("raw_value"),
            "historical_offset": meter.get("offset", 0.0),
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
