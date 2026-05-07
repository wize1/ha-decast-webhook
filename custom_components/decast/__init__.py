"""Decast Meter Webhook integration."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, SIGNAL_NEW_READING

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register the webhook and forward to the sensor platform."""
    webhook_id: str = entry.data[CONF_WEBHOOK_ID]

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    # Forward to platforms first so the dispatcher subscriber is in place
    # before any incoming webhook fires.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    webhook.async_register(
        hass,
        DOMAIN,
        "Decast",
        webhook_id,
        _make_webhook_handler(entry),
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down webhook and platforms."""
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Best-effort cleanup; webhook is already unregistered by unload."""
    return


def _make_webhook_handler(entry: ConfigEntry):
    """Build a webhook handler bound to this config entry."""

    async def handle_webhook(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        try:
            payload: dict[str, Any] = await request.json()
        except ValueError:
            _LOGGER.warning("Decast webhook received non-JSON body")
            return web.Response(status=400, text="invalid json")

        parsed = _parse_payload(payload)
        if parsed is None:
            # Acknowledge unsupported payload types so the device doesn't retry,
            # but log them so unexpected variants surface during onboarding.
            _LOGGER.debug("Ignoring Decast payload: %s", payload)
            return web.Response(status=200)

        async_dispatcher_send(
            hass,
            SIGNAL_NEW_READING.format(entry_id=entry.entry_id),
            parsed,
        )
        return web.Response(status=200)

    return handle_webhook


def _parse_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a Decast LAST_READING payload into a flat dict.

    Returns None if the payload is missing required fields or has an
    unsupported `type`. We are deliberately strict on `value` (must parse as
    float) since downstream sensors store it as a numeric state.
    """
    if payload.get("type") != "LAST_READING":
        return None

    utility = payload.get("utility") or {}
    reading = payload.get("reading") or {}
    device = utility.get("meteringDevice") or {}

    serial = device.get("serialNumber")
    resource = utility.get("resource")
    raw_value = reading.get("value") if isinstance(reading, dict) else None

    if not serial or not resource or raw_value is None:
        return None

    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "Decast reading for %s/%s has non-numeric value %r",
            serial,
            resource,
            raw_value,
        )
        return None

    reading_time = _parse_reading_time(reading.get("time"), utility.get("timezone"))

    return {
        "serial": str(serial),
        "resource": resource,
        "value": value,
        "reading_time": reading_time,
        "utility": utility,
        "raw_reading": reading,
    }


def _parse_reading_time(
    raw_time: str | None, tz_name: str | None
) -> datetime | None:
    """Parse `reading.time` (naive ISO, in utility timezone) into UTC datetime."""
    if not raw_time:
        return None
    try:
        dt = datetime.fromisoformat(raw_time)
    except ValueError:
        _LOGGER.debug("Could not parse reading time %r", raw_time)
        return None

    if dt.tzinfo is None:
        try:
            tz = ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        dt = dt.replace(tzinfo=tz)
    return dt
