"""Number platform for Decast meter offset and price.

Per (serial, resource) we expose two writable Number entities under the
meter device:

* `…historical offset` — added to incoming readings so the sensor's
  displayed value (which feeds the Energy / Water dashboards) reflects the
  meter's "true" total, including consumption that predates the webhook.
* `…price` — currency per unit, intended to be consumed by the Energy
  dashboard's "Use an entity tracking the price" option.

Both values are restored across HA restarts via RestoreEntity.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import get_meter_state
from .const import (
    DOMAIN,
    MANUFACTURER,
    RESOURCE_CONFIG,
    SIGNAL_NEW_READING,
    SIGNAL_OFFSET_CHANGED,
)

_LOGGER = logging.getLogger(__name__)

_UID_SEP = "::"
_KIND_OFFSET = "offset"
_KIND_PRICE = "price"
_KINDS = (_KIND_OFFSET, _KIND_PRICE)


def _make_unique_id(serial: str, resource: str, kind: str) -> str:
    return f"{serial}{_UID_SEP}{resource}{_UID_SEP}{kind}"


def _parse_unique_id(uid: str) -> tuple[str, str, str] | None:
    parts = uid.split(_UID_SEP)
    if len(parts) != 3 or parts[2] not in _KINDS:
        return None
    return parts[0], parts[1], parts[2]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Restore previously-seen meter Numbers and listen for new ones."""
    numbers: dict[str, _DecastNumberBase] = {}

    ent_reg = er.async_get(hass)
    initial: list[_DecastNumberBase] = []
    seen: set[tuple[str, str, str]] = set()

    # 1. Numbers we created on prior runs.
    for ent_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent_entry.domain != "number":
            continue
        parsed = _parse_unique_id(ent_entry.unique_id)
        if parsed is None:
            continue
        serial, resource, kind = parsed
        if resource not in RESOURCE_CONFIG:
            continue
        n = _create(entry.entry_id, serial, resource, kind)
        numbers[ent_entry.unique_id] = n
        initial.append(n)
        seen.add((serial, resource, kind))

    # 2. Backfill: any sensor entity we own implies a meter that should also
    # have offset + price numbers. This covers two cases:
    #   - first install of this version on a HA that already had reading
    #     sensors (so the user gets the new entities without waiting for
    #     the next webhook),
    #   - a previously-disabled or partially-deleted state where the sensor
    #     is in the registry but the matching numbers aren't.
    for ent_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent_entry.domain != "sensor":
            continue
        parts = ent_entry.unique_id.split(_UID_SEP)
        if len(parts) != 2:
            continue
        serial, resource = parts
        if resource not in RESOURCE_CONFIG:
            continue
        for kind in _KINDS:
            key = (serial, resource, kind)
            if key in seen:
                continue
            n = _create(entry.entry_id, serial, resource, kind)
            uid = _make_unique_id(serial, resource, kind)
            numbers[uid] = n
            initial.append(n)
            seen.add(key)

    if initial:
        async_add_entities(initial)

    @callback
    def _handle_reading(data: dict[str, Any]) -> None:
        serial = data["serial"]
        resource = data["resource"]
        if resource not in RESOURCE_CONFIG:
            return

        new: list[_DecastNumberBase] = []
        for kind in _KINDS:
            uid = _make_unique_id(serial, resource, kind)
            if uid in numbers:
                continue
            n = _create(entry.entry_id, serial, resource, kind)
            numbers[uid] = n
            new.append(n)
        if new:
            async_add_entities(new)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_NEW_READING.format(entry_id=entry.entry_id),
            _handle_reading,
        )
    )


def _create(
    entry_id: str, serial: str, resource: str, kind: str
) -> "_DecastNumberBase":
    cfg = RESOURCE_CONFIG[resource]
    if kind == _KIND_OFFSET:
        return DecastOffsetNumber(entry_id, serial, resource, cfg)
    return DecastPriceNumber(entry_id, serial, resource, cfg)


class _DecastNumberBase(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0.0
    _attr_native_max_value = 1_000_000.0
    _attr_native_step = 0.0001
    _attr_entity_category = EntityCategory.CONFIG

    _kind: str = ""
    _state_field: str = ""

    def __init__(
        self,
        entry_id: str,
        serial: str,
        resource: str,
        cfg: dict[str, Any],
    ) -> None:
        self._entry_id = entry_id
        self._serial = serial
        self._resource = resource
        self._cfg = cfg
        self._attr_unique_id = _make_unique_id(serial, resource, self._kind)
        self._attr_translation_key = f"{cfg['key']}_{self._kind}"
        self._attr_native_value = 0.0
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            translation_key="meter",
            translation_placeholders={"serial": serial},
            manufacturer=MANUFACTURER,
            model="IoT meter",
            serial_number=serial,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "", "unknown", "unavailable"):
            try:
                self._attr_native_value = float(last.state)
            except (TypeError, ValueError):
                pass

        # Mirror the restored value into shared meter state so the sensor's
        # initial native_value calculation can pick it up.
        meter = get_meter_state(
            self.hass, self._entry_id, self._serial, self._resource
        )
        meter[self._state_field] = float(self._attr_native_value or 0.0)


class DecastOffsetNumber(_DecastNumberBase):
    """Constant added to incoming readings before they're displayed."""

    _kind = _KIND_OFFSET
    _state_field = "offset"
    _attr_icon = "mdi:counter"

    def __init__(
        self,
        entry_id: str,
        serial: str,
        resource: str,
        cfg: dict[str, Any],
    ) -> None:
        super().__init__(entry_id, serial, resource, cfg)
        self._attr_native_unit_of_measurement = cfg["unit"]

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        meter = get_meter_state(
            self.hass, self._entry_id, self._serial, self._resource
        )
        meter["offset"] = value
        self.async_write_ha_state()
        async_dispatcher_send(
            self.hass,
            SIGNAL_OFFSET_CHANGED.format(entry_id=self._entry_id),
            {"serial": self._serial, "resource": self._resource, "offset": value},
        )


class DecastPriceNumber(_DecastNumberBase):
    """Currency-per-unit, intended for the Energy dashboard's price field."""

    _kind = _KIND_PRICE
    _state_field = "price"
    _attr_icon = "mdi:cash"

    def __init__(
        self,
        entry_id: str,
        serial: str,
        resource: str,
        cfg: dict[str, Any],
    ) -> None:
        super().__init__(entry_id, serial, resource, cfg)
        self._attr_native_unit_of_measurement = cfg.get("price_unit")
        self._attr_native_step = 0.01

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        meter = get_meter_state(
            self.hass, self._entry_id, self._serial, self._resource
        )
        meter["price"] = value
        self.async_write_ha_state()
