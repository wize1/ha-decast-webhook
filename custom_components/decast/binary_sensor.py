"""Binary-sensor platform for the price-expired indicator."""
from __future__ import annotations

from datetime import date
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import get_meter_state
from .const import (
    DOMAIN,
    MANUFACTURER,
    RESOURCE_CONFIG,
    SIGNAL_EXPIRY_CHANGED,
    SIGNAL_NEW_READING,
    SIGNAL_PRICE_CHANGED,
)

_LOGGER = logging.getLogger(__name__)

_UID_SEP = "::"
_KIND = "price_expired"


def _make_unique_id(serial: str, resource: str) -> str:
    return f"{serial}{_UID_SEP}{resource}{_UID_SEP}{_KIND}"


def _parse_unique_id(uid: str) -> tuple[str, str] | None:
    parts = uid.split(_UID_SEP)
    if len(parts) != 3 or parts[2] != _KIND:
        return None
    return parts[0], parts[1]


def _is_expired(meter: dict[str, Any] | None) -> bool:
    if not meter:
        return False
    expiry: date | None = meter.get("price_expiry")
    if expiry is None:
        return False
    if date.today() < expiry:
        return False
    last_changed: date | None = meter.get("price_last_changed_at")
    if last_changed is not None and last_changed >= expiry:
        # User updated the price on or after the expiry day — treat as
        # acknowledged. Reminder stays silent until the user picks a new
        # expiry date in the future.
        return False
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    sensors: dict[tuple[str, str], DecastPriceExpiredBinarySensor] = {}

    ent_reg = er.async_get(hass)
    initial: list[DecastPriceExpiredBinarySensor] = []

    for ent_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent_entry.domain != "binary_sensor":
            continue
        parsed = _parse_unique_id(ent_entry.unique_id)
        if parsed is None:
            continue
        serial, resource = parsed
        if resource not in RESOURCE_CONFIG:
            continue
        b = DecastPriceExpiredBinarySensor(entry.entry_id, serial, resource)
        sensors[(serial, resource)] = b
        initial.append(b)

    # Backfill: ensure each known meter has the binary sensor.
    for ent_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent_entry.domain != "sensor":
            continue
        parts = ent_entry.unique_id.split(_UID_SEP)
        if len(parts) != 2:
            continue
        serial, resource = parts
        if resource not in RESOURCE_CONFIG:
            continue
        if (serial, resource) in sensors:
            continue
        b = DecastPriceExpiredBinarySensor(entry.entry_id, serial, resource)
        sensors[(serial, resource)] = b
        initial.append(b)

    if initial:
        async_add_entities(initial)

    @callback
    def _handle_reading(data: dict[str, Any]) -> None:
        serial = data["serial"]
        resource = data["resource"]
        if resource not in RESOURCE_CONFIG:
            return
        if (serial, resource) in sensors:
            return
        b = DecastPriceExpiredBinarySensor(entry.entry_id, serial, resource)
        sensors[(serial, resource)] = b
        async_add_entities([b])

    @callback
    def _refresh(data: dict[str, Any]) -> None:
        if (b := sensors.get((data["serial"], data["resource"]))) is not None:
            b.async_recompute()

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
            SIGNAL_EXPIRY_CHANGED.format(entry_id=entry.entry_id),
            _refresh,
        )
    )
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_PRICE_CHANGED.format(entry_id=entry.entry_id),
            _refresh,
        )
    )


class DecastPriceExpiredBinarySensor(BinarySensorEntity):
    """`on` while the tariff expiry has passed and the price hasn't been updated since."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:cash-clock"

    def __init__(self, entry_id: str, serial: str, resource: str) -> None:
        cfg = RESOURCE_CONFIG[resource]
        self._entry_id = entry_id
        self._serial = serial
        self._resource = resource
        self._attr_unique_id = _make_unique_id(serial, resource)
        self._attr_translation_key = f"{cfg['key']}_price_expired"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            translation_key="meter",
            translation_placeholders={"serial": serial},
            manufacturer=MANUFACTURER,
            model="IoT meter",
            serial_number=serial,
        )

    def _meter(self) -> dict[str, Any] | None:
        if self.hass is None:
            return None
        return get_meter_state(
            self.hass, self._entry_id, self._serial, self._resource
        )

    @property
    def is_on(self) -> bool:
        return _is_expired(self._meter())

    @callback
    def async_recompute(self) -> None:
        if self.hass is not None and self.entity_id is not None:
            self.async_write_ha_state()
