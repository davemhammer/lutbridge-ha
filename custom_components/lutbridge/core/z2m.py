# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Zigbee2MQTT request builders and snapshot parsers. Pure Python: a request is a (topic, payload dict) pair.

Zigbee2MQTT publishes retained snapshots on <base>/bridge/devices and <base>/bridge/groups, which is where
the current groups and binds come from, and takes requests on <base>/bridge/request/....
"""
from __future__ import annotations

from typing import Any

from . import planner

DEFAULT_BASE = "zigbee2mqtt"
CONVERTER_NAME = "pico_bridge.mjs"
CONVERTER_MODEL = "l-re.pico-bridge"


def converter_hash(code: str) -> str:
    """Short fingerprint of the converter source, to tell whether Zigbee2MQTT already has this exact version."""
    import hashlib

    return hashlib.sha1(code.encode()).hexdigest()[:12]


def request(base: str, path: str, payload: dict) -> tuple[str, dict]:
    return f"{base}/bridge/request/{path}", payload


def install_converter(base: str, code: str) -> tuple[str, dict]:
    return request(base, "converter/save", {"name": CONVERTER_NAME, "code": code})


def to_request(action: Any, bridge: str, base: str = DEFAULT_BASE) -> tuple[str, dict]:
    """The Zigbee2MQTT request that carries out one planner action. `bridge` is the C6's IEEE address."""
    A = planner
    if isinstance(action, A.CreateGroup):
        return request(base, "group/add", {"friendly_name": action.name})
    if isinstance(action, A.RemoveGroup):
        return request(base, "group/remove", {"id": action.name})
    if isinstance(action, A.AddMember):
        return request(base, "group/members/add", {"group": action.group, "device": action.ieee})
    if isinstance(action, A.Bind):
        return request(base, "device/bind", _bind(bridge, action))
    if isinstance(action, A.Unbind):
        return request(base, "device/unbind", _bind(bridge, action))
    if isinstance(action, A.SetSlot):
        return f"{base}/{bridge}/set", {"slot": {"index": action.index, "serial": action.serial, "mask": action.mask}}
    if isinstance(action, A.ClearSlot):
        return f"{base}/{bridge}/set", {"slot": {"index": action.index, "serial": "00000000", "mask": 0}}
    raise TypeError(f"unknown action {action!r}")


def _bind(bridge: str, a: Any) -> dict:
    return {
        "from": bridge,
        "from_endpoint": a.endpoint,
        "to": a.group,
        "clusters": list(planner.BIND_CLUSTERS),
    }


def claim(bridge: str, counter: int, base: str = DEFAULT_BASE, handled=None) -> tuple[str, dict]:
    """A heartbeat claim. With `handled` (slot indexes) naming only some slots, only those are claimed; claiming every
    slot is sent as the plain claim, which every firmware version understands."""
    if handled is None or set(int(s) for s in handled) >= set(range(planner.SLOT_COUNT)):
        return f"{base}/{bridge}/set", {"claim": counter & 0xFF}
    return f"{base}/{bridge}/set", {"claim": {"counter": counter & 0xFF, "slots": sorted(int(s) for s in handled)}}


def read_slots(bridge: str, base: str = DEFAULT_BASE) -> tuple[str, dict]:
    return f"{base}/{bridge}/get", {"slots": ""}


# ---- snapshots ---------------------------------------------------------------


def parse_groups(groups: list[dict]) -> tuple[dict, dict]:
    """(name -> frozenset of member IEEE addresses, group id -> name) from <base>/bridge/groups."""
    by_name: dict = {}
    by_id: dict = {}
    for g in groups:
        name = g.get("friendly_name")
        by_name[name] = frozenset(m.get("ieee_address") for m in g.get("members", []))
        by_id[g.get("id")] = name
    return by_name, by_id


def parse_binds(devices: list[dict], bridge: str, group_names: dict) -> dict:
    """endpoint -> set of group names, for the pool endpoints of the C6, from <base>/bridge/devices."""
    out: dict = {}
    for d in devices:
        if d.get("ieee_address") != bridge:
            continue
        for ep, info in (d.get("endpoints") or {}).items():
            for b in info.get("bindings", []):
                t = b.get("target", {})
                if t.get("type") == "group" and int(ep) >= planner.EP_BASE:
                    name = group_names.get(t.get("id"))
                    if name:
                        out.setdefault(int(ep), set()).add(name)
    return out


def parse_slots(state: dict) -> dict:
    """slot index -> (serial hex, mask) from the device state, skipping empty slots. Needs a prior read_slots."""
    out: dict = {}
    for i in range(planner.SLOT_COUNT):
        v = state.get(f"slot_{i}")
        if not v:
            continue
        serial, _, mask = v.partition(":")
        if serial and serial != "00000000":
            out[i] = (serial, int(mask, 16))
    return out


def _is_bridge(d: dict) -> bool:
    return (d.get("definition") or {}).get("model") == CONVERTER_MODEL or d.get("model_id") == CONVERTER_MODEL


def find_bridge(devices: list[dict]) -> str | None:
    """IEEE address of the pico bridge, found by its model identifier."""
    for d in devices:
        if _is_bridge(d):
            return d.get("ieee_address")
    return None


def find_bridge_name(devices: list[dict]) -> str | None:
    """Friendly name of the pico bridge, which is what its state and availability topics are named after."""
    for d in devices:
        if _is_bridge(d):
            return d.get("friendly_name") or d.get("ieee_address")
    return None


def bridge_has_current_converter(devices: list[dict]) -> bool | None:
    """True when Zigbee2MQTT runs the pico bridge through an external converter (ours), None if the bridge is unknown."""
    for d in devices:
        if _is_bridge(d):
            return (d.get("definition") or {}).get("source") == "external"
    return None


def is_claim_slots_rejection(payload: Any) -> bool:
    """True for a Zigbee2MQTT log entry saying the board refused the per-slot claim: old firmware answers the
    ClaimSlots command with UNSUP_COMMAND. Zigbee2MQTT publishes its log lines on <base>/bridge/logging."""
    if not isinstance(payload, dict):
        return False
    text = str(payload.get("message", ""))
    return "claimSlots" in text and "UNSUP_COMMAND" in text


def parse_online(payload: Any) -> bool | None:
    """Zigbee2MQTT publishes availability and bridge state as {"state": "online"} (older versions: a bare string)."""
    state = payload.get("state") if isinstance(payload, dict) else payload
    if state == "online":
        return True
    if state == "offline":
        return False
    return None
