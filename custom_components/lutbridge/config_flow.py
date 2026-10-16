# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import BRIDGE_REPO_URL, CONF_BASE, DEFAULT_BASE, DOMAIN


class LutBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Lutron Pico fallback", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_BASE, default=DEFAULT_BASE): str}),
            description_placeholders={"bridge_url": BRIDGE_REPO_URL},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return LutBridgeOptionsFlow()


class LutBridgeOptionsFlow(config_entries.OptionsFlow):
    """Configure shows what would change, in plain words, and applies it only if the owner says so."""

    async def async_step_init(self, user_input=None):
        runtime = self.config_entry.runtime_data
        errors: dict[str, str] = {}
        problem = ""
        if user_input is not None:
            if not user_input.get("apply"):
                return self.async_create_entry(data={})
            try:
                await runtime.async_apply()
                return self.async_create_entry(data={})
            except HomeAssistantError as err:
                errors["base"] = "apply_failed"
                problem = str(err)
        build = runtime.build if errors and runtime.build else await runtime.async_plan()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required("apply", default=False): bool}),
            description_placeholders={
                "review": build.review(runtime.light_names()),
                "plan_id": build.plan_id,
                "problem": problem,
            },
            errors=errors,
        )
