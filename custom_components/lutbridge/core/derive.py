# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Derive what each Pico button is meant to control from existing Home Assistant automations.

Deterministic rules, no language model. Two shapes are understood:

  R1  a lutron_caseta device trigger on a Pico (device_id + subtype) whose actions call a light, switch or
      homeassistant on/off/toggle service on entities;
  R2  the smartqasa/lutron_pico_5_light blueprint (pico + entity list).

Anything else that mentions a Pico is reported as unmapped, with the reason, instead of guessed at. Automation
conditions are ignored on purpose (the fallback is plain on/off/dim) but flagged, so the plan can show them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from . import pico

LIGHT_SERVICES = {
    "light.turn_on", "light.turn_off", "light.toggle",
    "homeassistant.turn_on", "homeassistant.turn_off", "homeassistant.toggle",
    "switch.turn_on", "switch.turn_off", "switch.toggle",
}

# Blueprints whose inputs we understand: path suffix -> buttons the blueprint wires to its entity list.
BLUEPRINTS = {
    "lutron_pico_5_light.yaml": pico.ON | pico.OFF | pico.RAISE | pico.LOWER,
}


@dataclass
class ButtonTarget:
    mask: int
    entities: set[str] = field(default_factory=set)
    automations: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)


@dataclass
class Unmapped:
    automation: str
    device_id: str | None
    reason: str


@dataclass
class PicoIntent:
    device_id: str
    # One entry per single button bit, because different buttons of a Pico can target different lights.
    targets: dict[int, ButtonTarget] = field(default_factory=dict)

    def add(self, mask: int, entities: Iterable[str], automation: str, flags: Iterable[str] = ()) -> None:
        for bit in (pico.ON, pico.OFF, pico.RAISE, pico.LOWER, pico.FAVORITE):
            if mask & bit:
                t = self.targets.setdefault(bit, ButtonTarget(bit))
                t.entities.update(entities)
                t.automations.add(automation)
                t.flags.update(flags)


def _listify(v: Any) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple, set)) else [v]


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return None


def _name(auto: dict) -> str:
    return str(auto.get("alias") or auto.get("id") or "?")


def _pico_triggers(auto: dict, pico_ids: set[str]) -> list[dict]:
    """Device triggers in this automation that point at a known Pico."""
    out = []
    for t in _listify(_first(auto, "triggers", "trigger")):
        if not isinstance(t, dict):
            continue
        kind = t.get("trigger") or t.get("platform")
        if kind == "device" and t.get("domain") == "lutron_caseta" and t.get("device_id") in pico_ids:
            out.append(t)
    return out


def _entity_ids(action: dict) -> list[str]:
    ids: list[str] = []
    for container in (action.get("target"), action.get("data"), action):
        if isinstance(container, dict) and "entity_id" in container:
            ids += [e for e in _listify(container["entity_id"]) if isinstance(e, str)]
    return ids


def _actions(auto: dict) -> tuple[list[str], set[str]]:
    """Light entities the automation switches, and flags for anything that is not a plain light call."""
    entities: list[str] = []
    flags: set[str] = set()
    for a in _listify(_first(auto, "actions", "action")):
        if not isinstance(a, dict):
            flags.add("complex")
            continue
        service = a.get("action") or a.get("service")
        if service in LIGHT_SERVICES:
            entities += _entity_ids(a)
        else:
            flags.add("complex")  # scenes, scripts, delays, choose blocks: not reproduced by the fallback
    return entities, flags


def derive(automations: Iterable[dict], pico_ids: Iterable[str]) -> tuple[dict[str, PicoIntent], list[Unmapped]]:
    """Return per-Pico intent keyed by HA device_id, and the automations that could not be understood."""
    ids = set(pico_ids)
    intents: dict[str, PicoIntent] = {}
    unmapped: list[Unmapped] = []

    for auto in automations:
        name = _name(auto)
        flags = {"conditions"} if _listify(_first(auto, "conditions", "condition")) else set()

        bp = auto.get("use_blueprint")
        if isinstance(bp, dict):
            inputs = bp.get("input") or {}
            picos = [v for k, v in inputs.items() if k.startswith("pico") and isinstance(v, str) and v in ids]
            if not picos:
                continue
            suffix = str(bp.get("path", "")).rsplit("/", 1)[-1]
            mask = BLUEPRINTS.get(suffix)
            entities = [e for e in _listify(inputs.get("entity")) if isinstance(e, str)]
            if mask is None or not entities:
                unmapped.append(Unmapped(name, picos[0], f"blueprint {bp.get('path')!r} is not understood"))
                continue
            for dev in set(picos):
                intents.setdefault(dev, PicoIntent(dev)).add(mask, entities, name, flags | {"blueprint"})
            continue

        triggers = _pico_triggers(auto, ids)
        if not triggers:
            continue
        entities, aflags = _actions(auto)
        for t in triggers:
            mask = pico.SUBTYPE_MASK.get(str(t.get("subtype")))
            if mask is None:
                unmapped.append(Unmapped(name, t.get("device_id"), f"unknown button {t.get('subtype')!r}"))
            elif not entities:
                unmapped.append(Unmapped(name, t.get("device_id"), "no light actions"))
            else:
                intents.setdefault(t["device_id"], PicoIntent(t["device_id"])).add(mask, entities, name, flags | aflags)
    return intents, unmapped
