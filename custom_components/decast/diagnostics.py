"""Diagnostics support for Decast Meter Webhook.

Exposes the in-memory ring buffer of recent webhook events via the
"Download diagnostics" button on the integration card. Sensitive values
(`contractAccount`, address `fullValue`) are redacted.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, WEBHOOK_LOG_MAX

# Field names that may carry PII. async_redact_data walks nested dicts so
# we don't need to know exactly where these appear in the payload tree.
TO_REDACT = {"contractAccount", "fullValue"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Decast config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}) or {}
    log = list(entry_data.get("webhook_log") or [])

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    devices = [
        {
            "id": d.id,
            "name": d.name,
            "manufacturer": d.manufacturer,
            "model": d.model,
            "serial_number": d.serial_number,
            "identifiers": list(d.identifiers),
        }
        for d in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
    ]

    entities = [
        {
            "entity_id": e.entity_id,
            "unique_id": e.unique_id,
            "platform": e.platform,
            "device_class": e.device_class or e.original_device_class,
            "unit_of_measurement": e.unit_of_measurement,
            "disabled_by": e.disabled_by,
        }
        for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    ]

    return async_redact_data(
        {
            "config_entry": {
                "title": entry.title,
                "version": entry.version,
            },
            "webhook_log": {
                "max_entries": WEBHOOK_LOG_MAX,
                "count": len(log),
                "entries": log,
            },
            "devices": devices,
            "entities": entities,
        },
        TO_REDACT,
    )
