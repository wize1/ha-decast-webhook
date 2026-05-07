# Decast Meter Webhook for Home Assistant

A custom Home Assistant integration that receives meter readings from
[Decast IoT][decast] meters via the [Decast webhook API][spec] and exposes
them as native sensor entities.

The integration is push-only: Decast's cloud sends a `POST` to a webhook URL
that Home Assistant generates for you. No polling, no API key, no outbound
calls from HA.

## What you get

- One **device** per physical meter, identified by the meter's serial number.
- One **sensor** per resource type on that meter, with the right device class
  and unit so it slots straight into the Energy or Water dashboard:

  | Decast `resource` | Device class  | Unit |
  | ----------------- | ------------- | ---- |
  | `COLD_WATER`      | `water`       | m³   |
  | `HOT_WATER`       | `water`       | m³   |
  | `ELECTRICITY`     | `energy`      | kWh  |
  | `GAS`             | `gas`         | m³   |
  | `HEATING`         | _(none)_      | Gcal |

  All sensors use `state_class: total_increasing`.

- Address, place, contract account, timezone, and the original reading
  timestamp are surfaced as entity attributes. Electricity tariff fields
  (`tariff1Value` … `tariff4Value`) are also exposed as attributes when the
  meter reports them.

- Two writable **Number** entities per (meter, resource) under the same
  device, both restored across restarts:

  | Entity                         | Default | Purpose |
  | ------------------------------ | ------- | ------- |
  | `… historical offset`          | `0`     | Added to the raw webhook value before it's displayed. Use this to seed the meter with consumption that predates the integration (e.g. previous manual readings). |
  | `… price`                      | `0`     | Currency per unit (default `₽/m³`, `₽/kWh`, `₽/Gcal`). Wire this into the Energy dashboard's *Use an entity tracking the price* option. |

  The reading sensor's value is `raw_value + historical_offset`. The raw
  webhook value and current offset are both exposed as sensor attributes
  (`raw_value`, `historical_offset`).

  > **Set the offset once at setup.** The reading sensor uses
  > `state_class: total_increasing`, so changing the offset later can show
  > as a meter reset (if you decrease it) or as a fake spike of consumption
  > (if you increase it) in HA's long-term statistics. If you must change
  > it later, also reset statistics for the affected sensor under
  > *Developer Tools → Statistics*.

## Install

### Via HACS (recommended)

1. In HACS, add this repository as a **custom repository** with category
   _Integration_.
2. Install **Decast Meter Webhook**.
3. Restart Home Assistant.

### Manual

Copy [`custom_components/decast`](custom_components/decast/) into your Home
Assistant `config/custom_components/` directory and restart.

## Configure

1. **Settings → Devices & Services → Add Integration → Decast Meter Webhook**.
2. The dialog shows a webhook URL — copy it.
3. Paste the URL into your Decast account's webhook configuration.
4. Press **Submit**.

That's it. The first time each meter posts a reading, a device + sensor pair
appears automatically (along with the offset and price Number entities).
Subsequent readings update the existing sensor.

### Wiring up the Energy dashboard

After your first reading arrives:

1. *Settings → Dashboards → Energy*
2. For water: add the cold-water sensor (e.g. `sensor.decast_meter_222444_cold_water`)
   under **Water sources**. For the cost, choose
   **Use an entity tracking the price** and pick the matching `… price`
   number entity (e.g. `number.decast_meter_222444_cold_water_price`).
3. For electricity: same flow under **Electricity grid → Grid consumption**.
4. Set the historical offset in the corresponding Number entity if your
   meter was already accumulating before the integration was set up.

> **Public reachability.** Decast's cloud needs to reach Home Assistant from
> the internet. If you don't already expose HA via Nabu Casa or a reverse
> proxy, the URL won't be deliverable. Nabu Casa Cloud is the easiest option;
> it provides webhook URLs without any port forwarding.

## Branding

The Decast logo PNGs used by Home Assistant's UI live in
[`brand/`](brand/). Home Assistant doesn't read these from your integration
repo — it fetches them from `brands.home-assistant.io/{domain}/icon.png`.
Those URLs are populated by the
[`home-assistant/brands`](https://github.com/home-assistant/brands) repo.

To make HA and HACS show the Decast logo:

1. Fork [`home-assistant/brands`](https://github.com/home-assistant/brands).
2. Create the directory `custom_integrations/decast/` in your fork.
3. Copy these four files from [`brand/`](brand/) into that directory:
   `icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png`.
4. Open a PR to the upstream repo titled
   `Add Decast Meter Webhook (custom)`.

After it merges, the Decast logo will appear automatically in *Settings →
Devices & Services* and in the HACS card. No change to this integration is
needed.

## Webhook contract

The integration consumes the `LAST_READING` variant of the [Decast webhook
schema][spec]:

```json
{
  "type": "LAST_READING",
  "utility": {
    "resource": "COLD_WATER",
    "location": { "fullValue": "Россия, Москва, Хорошёвское шоссе, 68к1" },
    "place": "77",
    "contractAccount": "123456789",
    "timezone": "Europe/Moscow",
    "meteringDevice": { "serialNumber": "222444" }
  },
  "reading": { "time": "2020-08-23T21:00:00", "value": "461.0490" }
}
```

The reading `time` is treated as naive local time in the utility's
`timezone` and converted to a timezone-aware datetime before being stored as
the `reading_time` attribute.

Payloads with `type` other than `LAST_READING`, or missing required fields
(`utility.meteringDevice.serialNumber`, `utility.resource`, `reading.value`),
are acknowledged with `200` but otherwise ignored.

## Viewing the webhook log

Three ways to inspect what Decast actually sent you:

- **Live tail.** Open *Developer Tools → Events*, subscribe to event type
  `decast_webhook_received`, and click **Start Listening**. Every incoming
  webhook fires here — including ones that were rejected (`status: rejected`)
  or ignored (`status: ignored`) — so you can debug a misconfigured device
  without restarting HA.

- **Recent history.** *Settings → Devices & Services → Decast Meter Webhook
  → ⋮ menu on the integration card → Download diagnostics.* Returns a JSON
  file with the last 50 webhook events plus device/entity registry info.
  `contractAccount` and address fields are redacted.

- **Automations.** Trigger off the same event:

  ```yaml
  trigger:
    - platform: event
      event_type: decast_webhook_received
      event_data:
        status: accepted
  action:
    - service: notify.persistent_notification
      data:
        message: >-
          {{ trigger.event.data.parsed.resource }}:
          {{ trigger.event.data.parsed.value }}
  ```

The ring buffer is in-memory only (cleared on HA restart) and capped at 50
entries — it's a flight recorder, not a permanent log.

## Testing locally

Once the integration is configured, you can simulate a reading with `curl`:

```bash
curl -X POST <webhook-url> \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "LAST_READING",
    "utility": {
      "resource": "COLD_WATER",
      "place": "Test",
      "timezone": "UTC",
      "meteringDevice": { "serialNumber": "TEST-001" }
    },
    "reading": { "time": "2025-01-01T00:00:00", "value": "1.2345" }
  }'
```

A new device `Decast meter TEST-001` with a single `Cold water` sensor will
appear under the integration.

[decast]: https://decast.com/
[spec]: https://api.iot.decast.com/ui/webhook-openapi.yaml
