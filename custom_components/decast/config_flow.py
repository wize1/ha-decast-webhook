"""Config flow for Decast Meter Webhook."""
from __future__ import annotations

from typing import Any

from homeassistant.components import webhook
from homeassistant.components.webhook import async_generate_url
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_WEBHOOK_ID

from .const import DOMAIN


class DecastConfigFlow(ConfigFlow, domain=DOMAIN):
    """One-step UI flow that mints a webhook URL."""

    VERSION = 1

    def __init__(self) -> None:
        # Generated lazily on the first form view and reused on submit so the
        # URL the user sees is the one we store.
        self._webhook_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if self._webhook_id is None:
            self._webhook_id = webhook.async_generate_id()

        if user_input is None:
            url = async_generate_url(self.hass, self._webhook_id)
            return self.async_show_form(
                step_id="user",
                description_placeholders={"webhook_url": url},
            )

        return self.async_create_entry(
            title="Decast",
            data={CONF_WEBHOOK_ID: self._webhook_id},
        )
