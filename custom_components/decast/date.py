"""Date platform for Decast meter price-expiry picker."""
from __future__ import annotations

from datetime import date
import logging
from typing import Any

from homeassistant.components.date import DateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import get_meter_state
from .const import (
    DOMAIN,
    MANUFACTURER,
    RESOURCE_CONFIG,
    SIGNAL_EXPIRY_CHANGED,
    SIGNAL_NEW_READING,
)

_LOGGER = logging.getLogger(__name__)

_UID_SEP = "::"
_KIND = "price_expiry"


def _make_unique_id(serial: str, resource: str) -> str:
    return f"{serial}{_UID_SEP}{resource}{_UID_SEP}{_KIND}"


def _parse_unique_id(uid: str) -> tuple[str, str] | None:
    parts = uid.split(_UID_SEP)
    if len(parts) != 3 or parts[2] != _KIND:
        return None
    return parts[0], parts[1]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    dates: dict[tuple[str, str], DecastPriceExpiryDate] = {}

    ent_reg = er.async_get(hass)
    initial: list[DecastPriceExpiryDate] = []

    # 1. Restore previously-seen expiry dates.
    for ent_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent_entry.domain != "date":
            continue
        parsed = _parse_unique_id(ent_entry.unique_id)
        if parsed is None:
            continue
        serial, resource = parsed
        if resource not in RESOURCE_CONFIG:
            continue
        d = DecastPriceExpiryDate(entry.entry_id, serial, resource)
        dates[(serial, resource)] = d
        initial.append(d)

    # 2. Backfill: every meter we already track (via its sensor entity) gets
    # an expiry date entity. Covers upgrade-in-place.
    for ent_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent_entry.domain != "sensor":
            continue
        parts = ent_entry.unique_id.split(_UID_SEP)
        # The reading sensor uses {serial}::{resource}; the price-mirror
        # sensor uses {serial}::{resource}::price — skip the latter.
        if len(parts) != 2:
            continue
        serial, resource = parts
        if resource not in RESOURCE_CONFIG:
            continue
        if (serial, resource) in dates:
            continue
        d = DecastPriceExpiryDate(entry.entry_id, serial, resource)
        dates[(serial, resource)] = d
        initial.append(d)

    if initial:
        async_add_entities(initial)

    @callback
    def _handle_reading(data: dict[str, Any]) -> None:
        serial = data["serial"]
        resource = data["resource"]
        if resource not in RESOURCE_CONFIG:
            return
        if (serial, resource) in dates:
            return
        d = DecastPriceExpiryDate(entry.entry_id, serial, resource)
        dates[(serial, resource)] = d
        async_add_entities([d])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_NEW_READING.format(entry_id=entry.entry_id),
            _handle_reading,
        )
    )


class DecastPriceExpiryDate(DateEntity, RestoreEntity):
    """User-set date for when the current tariff expires."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry_id: str, serial: str, resource: str) -> None:
        cfg = RESOURCE_CONFIG[resource]
        self._entry_id = entry_id
        self._serial = serial
        self._resource = resource
        self._attr_unique_id = _make_unique_id(serial, resource)
        self._attr_translation_key = f"{cfg['key']}_price_expiry"
        self._attr_native_value = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            translation_key="meter",
            translation_placeholders={"serial": serial},
            manufacturer=MANUFACTURER,
            model="IoT meter",
            serial_number=serial,
        )

    async def async_set_value(self, value: date) -> None:
        self._attr_native_value = value
        meter = get_meter_state(
            self.hass, self._entry_id, self._serial, self._resource
        )
        meter["price_expiry"] = value
        self.async_write_ha_state()
        async_dispatcher_send(
            self.hass,
            SIGNAL_EXPIRY_CHANGED.format(entry_id=self._entry_id),
            {"serial": self._serial, "resource": self._resource},
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "", "unknown", "unavailable"):
            try:
                self._attr_native_value = date.fromisoformat(last.state)
            except (TypeError, ValueError):
                self._attr_native_value = None

        meter = get_meter_state(
            self.hass, self._entry_id, self._serial, self._resource
        )
        meter["price_expiry"] = self._attr_native_value

        # Re-publish so the binary_sensor and reminder loop pick up the
        # restored value regardless of platform setup ordering.
        async_dispatcher_send(
            self.hass,
            SIGNAL_EXPIRY_CHANGED.format(entry_id=self._entry_id),
            {"serial": self._serial, "resource": self._resource},
        )
