"""Decast Meter Webhook integration."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import logging
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DATA_METERS,
    DOMAIN,
    EVENT_WEBHOOK_RECEIVED,
    SIGNAL_NEW_READING,
    WEBHOOK_LOG_MAX,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER]

DATA_WEBHOOK_LOG = "webhook_log"


def get_meter_state(
    hass: HomeAssistant, entry_id: str, serial: str, resource: str
) -> dict[str, Any]:
    """Return (creating if needed) the shared per-meter state dict.

    Both sensor and number platforms call this — the dict carries the latest
    raw webhook value, the user-set historical offset, and the user-set
    price. Reads/writes are all on the event loop thread, so no locking.
    """
    meters = hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {}).setdefault(
        DATA_METERS, {}
    )
    return meters.setdefault(
        (serial, resource),
        {"raw_value": None, "offset": 0.0, "price": 0.0},
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register the webhook and forward to the sensor platform."""
    webhook_id: str = entry.data[CONF_WEBHOOK_ID]

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_WEBHOOK_LOG: deque(maxlen=WEBHOOK_LOG_MAX),
        DATA_METERS: {},
    }

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
        # Read the raw body first so we can log it even if JSON parsing fails.
        raw_body = await request.text()

        try:
            payload = json.loads(raw_body)
        except ValueError:
            _LOGGER.warning("Decast webhook received non-JSON body")
            _record(
                hass,
                entry,
                status="rejected",
                reason="invalid_json",
                payload=None,
                raw_body=raw_body,
                parsed=None,
            )
            return web.Response(status=400, text="invalid json")

        parsed = _parse_payload(payload)

        if parsed is None:
            _LOGGER.debug("Ignoring Decast payload: %s", payload)
            _record(
                hass,
                entry,
                status="ignored",
                reason=_ignore_reason(payload),
                payload=payload,
                raw_body=None,
                parsed=None,
            )
            # Acknowledge so the device doesn't retry forever.
            return web.Response(status=200)

        _record(
            hass,
            entry,
            status="accepted",
            reason=None,
            payload=payload,
            raw_body=None,
            parsed=parsed,
        )

        # Stash the raw value in shared state before dispatching, so the
        # sensor and number entities all see the new reading on lookup.
        meter = get_meter_state(
            hass, entry.entry_id, parsed["serial"], parsed["resource"]
        )
        meter["raw_value"] = parsed["value"]

        async_dispatcher_send(
            hass,
            SIGNAL_NEW_READING.format(entry_id=entry.entry_id),
            parsed,
        )
        return web.Response(status=200)

    return handle_webhook


def _ignore_reason(payload: dict[str, Any]) -> str:
    """Best-effort label for why a parsed-but-unaccepted payload was dropped."""
    if not isinstance(payload, dict):
        return "not_object"
    payload_type = payload.get("type")
    if payload_type != "LAST_READING":
        return f"unsupported_type:{payload_type!r}"
    utility = payload.get("utility") or {}
    if not (utility.get("meteringDevice") or {}).get("serialNumber"):
        return "missing_serial_number"
    if not utility.get("resource"):
        return "missing_resource"
    reading = payload.get("reading") or {}
    if not isinstance(reading, dict) or reading.get("value") in (None, ""):
        return "missing_value"
    return "unparseable_value"


def _record(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    status: str,
    reason: str | None,
    payload: dict[str, Any] | None,
    raw_body: str | None,
    parsed: dict[str, Any] | None,
) -> None:
    """Append a webhook event to the ring buffer and fire it on the event bus.

    `parsed` carries `datetime` values which we serialise to ISO so the entry
    is JSON-friendly (event listeners + diagnostics consumers).
    """
    received_at = datetime.now(timezone.utc)
    parsed_serialisable = _serialise_parsed(parsed) if parsed is not None else None

    log_entry: dict[str, Any] = {
        "received_at": received_at.isoformat(),
        "status": status,
    }
    if reason is not None:
        log_entry["reason"] = reason
    if payload is not None:
        log_entry["payload"] = payload
    elif raw_body is not None:
        log_entry["raw_body"] = raw_body[:2000]
    if parsed_serialisable is not None:
        log_entry["parsed"] = parsed_serialisable

    buf = (
        hass.data.get(DOMAIN, {})
        .get(entry.entry_id, {})
        .get(DATA_WEBHOOK_LOG)
    )
    if buf is not None:
        buf.append(log_entry)

    hass.bus.async_fire(EVENT_WEBHOOK_RECEIVED, {"entry_id": entry.entry_id, **log_entry})


def _serialise_parsed(parsed: dict[str, Any]) -> dict[str, Any]:
    """Make the parsed dict JSON-friendly (datetime → ISO string)."""
    out = dict(parsed)
    if isinstance(rt := out.get("reading_time"), datetime):
        out["reading_time"] = rt.isoformat()
    return out


def _parse_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a Decast LAST_READING payload into a flat dict.

    Returns None if the payload is missing required fields or has an
    unsupported `type`. We are deliberately strict on `value` (must parse as
    float) since downstream sensors store it as a numeric state.
    """
    if not isinstance(payload, dict) or payload.get("type") != "LAST_READING":
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
