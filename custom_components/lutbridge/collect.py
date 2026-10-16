# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Gather what the planner needs from Home Assistant's registries and files. The only module that reads HA state."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.yaml import load_yaml

from .core import pico
from .core.pipeline import PicoInfo


def _identifier(dev, domain: str, prefix: str = ""):
    """Second element of the first device identifier in `domain` (identifiers are not always pairs)."""
    for ident in dev.identifiers:
        if len(ident) >= 2 and ident[0] == domain and str(ident[1]).startswith(prefix):
            return ident[1]
    return None


async def load_automations(hass: HomeAssistant) -> list[dict]:
    """UI-managed automations live in automations.yaml, with blueprint inputs resolved by name."""
    path = hass.config.path("automations.yaml")

    def _read() -> list:
        try:
            data = load_yaml(path)
        except FileNotFoundError:
            return []
        return data if isinstance(data, list) else []

    return [a for a in await hass.async_add_executor_job(_read) if isinstance(a, dict)]


def _all_devices(registry) -> list:
    """Every device entry. Newer Home Assistant wants the registry iterated (which yields entries) instead of used as
    a mapping; older versions only offer the mapping. Try the supported way first so no deprecation is triggered."""
    items = registry.devices
    first = next(iter(items), None)
    if first is None:
        return []
    return list(items) if hasattr(first, "identifiers") else list(items.values())


def pico_devices(hass: HomeAssistant) -> dict[str, PicoInfo]:
    """Every lutron_caseta Pico, keyed by Home Assistant device id."""
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)
    out: dict[str, PicoInfo] = {}
    for dev in _all_devices(devices):
        ident = _identifier(dev, "lutron_caseta")
        if ident is None or not pico.is_pico_model(dev.model):
            continue
        try:
            serial = pico.serial_hex(ident)
        except (TypeError, ValueError):
            continue
        area = areas.async_get_area(dev.area_id) if dev.area_id else None
        out[dev.id] = PicoInfo(dev.id, serial, dev.name_by_user or dev.name or serial, area.name if area else None, dev.model)
    return out


def resolvers(hass: HomeAssistant, z2m_groups: dict):
    """(members_of, z2m_of) for the planner.

    members_of expands Home Assistant light groups. z2m_of says which Zigbee2MQTT devices an entity stands for: a
    device by IEEE address, or a Zigbee2MQTT group by its member list from the bridge/groups snapshot.
    """
    entities = er.async_get(hass)
    devices = dr.async_get(hass)

    def members_of(entity_id: str):
        entry = entities.async_get(entity_id)
        if entry and entry.platform == "group":
            state = hass.states.get(entity_id)
            return list(state.attributes.get("entity_id") or []) if state else []
        return None

    def z2m_of(entity_id: str):
        entry = entities.async_get(entity_id)
        if not entry or entry.platform != "mqtt" or not entry.device_id:
            return None
        dev = devices.async_get(entry.device_id)
        if not dev:
            return None
        ident = _identifier(dev, "mqtt", "zigbee2mqtt_")
        if ident is None:
            return None
        if dev.model == "Group":
            return z2m_groups.get(dev.name) or None
        rest = str(ident)[len("zigbee2mqtt_"):]
        return frozenset({rest}) if rest.startswith("0x") else None

    return members_of, z2m_of


def automation_problem_map(hass: HomeAssistant, aliases) -> dict[str, str]:
    """alias -> what is wrong with it, for those of the automations the applied mapping came from that are missing or
    switched off right now. Healthy automations are absent."""
    states = {s.attributes.get("friendly_name"): s.state for s in hass.states.async_all("automation")}
    problems: dict[str, str] = {}
    for alias in sorted(set(aliases)):
        if alias not in states:
            problems[alias] = f"automation '{alias}' no longer exists"
        elif states[alias] != "on":
            problems[alias] = f"automation '{alias}' is {states[alias]}"
    return problems


def automation_problems(hass: HomeAssistant, aliases: set[str]) -> list[str]:
    """The same problems as sentences, in alias order."""
    return list(automation_problem_map(hass, aliases).values())
