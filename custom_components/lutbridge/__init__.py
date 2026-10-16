# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Lutron Pico Fallback Bridge.

Derives which Zigbee lights each Lutron Pico controls from existing automations, programs that into the ESP32-C6
bridge through Zigbee2MQTT, and keeps it disarmed while Home Assistant and those automations are healthy.
The logic lives in core/ (pure Python, unit-tested); this package is the Home Assistant wiring.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, PLATFORMS
from .runtime import Runtime

type LutBridgeConfigEntry = ConfigEntry[Runtime]


async def async_setup_entry(hass: HomeAssistant, entry: LutBridgeConfigEntry) -> bool:
    runtime = Runtime(hass, entry)
    await runtime.async_setup()
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LutBridgeConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_unload()
        for name in ("plan", "apply", "install_converter"):
            hass.services.async_remove(DOMAIN, name)
    return ok


def _runtime(hass: HomeAssistant) -> Runtime:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0].runtime_data


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "plan"):
        return

    async def plan(call: ServiceCall) -> None:
        await _runtime(hass).async_plan(bool(call.data.get("rewrite_slots", False)))

    async def apply(call: ServiceCall) -> None:
        await _runtime(hass).async_apply(call.data.get("plan_id"))

    async def install_converter(call: ServiceCall) -> None:
        await _runtime(hass).async_install_converter()

    hass.services.async_register(DOMAIN, "plan", plan, schema=vol.Schema({vol.Optional("rewrite_slots"): bool}))
    hass.services.async_register(DOMAIN, "apply", apply, schema=vol.Schema({vol.Optional("plan_id"): str}))
    hass.services.async_register(DOMAIN, "install_converter", install_converter)
