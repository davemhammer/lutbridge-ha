# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""A small fake of Zigbee2MQTT behind the Mqtt protocol, for testing the orchestrator without hardware.

It models groups, bindings, the pico bridge's slot table (including the C6 refusing overlapping button masks for
one serial), the retained devices/groups snapshots, and request/response with `transaction` echo.
"""
from __future__ import annotations

import fnmatch

BRIDGE = "0x58e6c5fffe191384"
BASE = "zigbee2mqtt"


class FakeZ2M:
    def __init__(self, fail_on: str | None = None):
        self.groups: dict[int, dict] = {}   # id -> {"name", "members": set}
        self.binds: dict[int, set[int]] = {}  # endpoint -> group ids
        self.slots: dict[int, tuple[str, int]] = {}
        self.published: list[tuple[str, dict]] = []
        self.claims: list = []              # every claim payload sent to the board
        self.availability: bool | None = None   # the board's availability topic; None = Zigbee2MQTT availability is off
        self.z2m_state: bool | None = True      # <base>/bridge/state
        self.fail_on = fail_on              # request path that answers with an error
        self._subs: list[tuple[str, object]] = []
        self._next_id = 20

    # ---- the Mqtt protocol --------------------------------------------------------

    async def subscribe(self, topic, callback):
        self._subs.append((topic, callback))
        self._replay(topic, callback)
        return lambda: self._subs.remove((topic, callback))

    async def publish(self, topic, payload):
        self.published.append((topic, payload))
        if topic.startswith(f"{BASE}/bridge/request/"):
            self._request(topic.removeprefix(f"{BASE}/bridge/request/"), dict(payload))
        elif topic == f"{BASE}/{BRIDGE}/set" and "claim" in payload:
            self.claims.append(payload["claim"])
        elif topic == f"{BASE}/{BRIDGE}/set" and "slot" in payload:
            self._set_slot(payload["slot"])
        elif topic == f"{BASE}/{BRIDGE}/get" and "slots" in payload:
            self._emit(f"{BASE}/{BRIDGE}", self._slot_state())

    # ---- plumbing -----------------------------------------------------------------

    def _emit(self, topic, payload):
        for pattern, cb in list(self._subs):
            if fnmatch.fnmatch(topic, pattern.replace("#", "*")):
                cb(topic, payload)

    def _replay(self, pattern, cb):
        if pattern == f"{BASE}/bridge/devices":
            cb(pattern, self.devices_payload())
        elif pattern == f"{BASE}/bridge/groups":
            cb(pattern, self.groups_payload())
        elif pattern == f"{BASE}/bridge/state" and self.z2m_state is not None:
            cb(pattern, {"state": "online" if self.z2m_state else "offline"})
        elif pattern == f"{BASE}/{BRIDGE}/availability" and self.availability is not None:
            cb(pattern, {"state": "online" if self.availability else "offline"})

    def set_availability(self, online: bool) -> None:
        self.availability = online
        self._emit(f"{BASE}/{BRIDGE}/availability", {"state": "online" if online else "offline"})

    def emit_log(self, message: str, level: str = "error") -> None:
        self._emit(f"{BASE}/bridge/logging", {"level": level, "message": message, "namespace": "z2m"})

    def set_z2m_state(self, online: bool) -> None:
        self.z2m_state = online
        self._emit(f"{BASE}/bridge/state", {"state": "online" if online else "offline"})

    def devices_payload(self):
        endpoints = {"1": {"bindings": []}}
        for ep, gids in self.binds.items():
            endpoints[str(ep)] = {"bindings": [{"cluster": c, "target": {"type": "group", "id": g}}
                                               for g in sorted(gids) for c in ("genOnOff", "genLevelCtrl")]}
        return [{"ieee_address": BRIDGE, "friendly_name": BRIDGE, "model_id": "l-re.pico-bridge", "endpoints": endpoints},
                {"ieee_address": "0x1", "endpoints": {}}]

    def groups_payload(self):
        return [{"id": i, "friendly_name": g["name"], "members": [{"ieee_address": m, "endpoint": 11} for m in sorted(g["members"])]}
                for i, g in sorted(self.groups.items())]

    def _snapshots(self):
        self._emit(f"{BASE}/bridge/devices", self.devices_payload())
        self._emit(f"{BASE}/bridge/groups", self.groups_payload())

    def _gid(self, name):
        return next((i for i, g in self.groups.items() if g["name"] == name), None)

    def _respond(self, path, tx, status="ok", error=None):
        body = {"data": {}, "status": status, "transaction": tx}
        if error:
            body["error"] = error
        self._emit(f"{BASE}/bridge/response/{path}", body)

    # ---- behaviour ----------------------------------------------------------------

    def _request(self, path, p):
        tx = p.get("transaction")
        if self.fail_on == path:
            return self._respond(path, tx, "error", f"simulated failure of {path}")
        if path == "group/add":
            self.groups[self._next_id] = {"name": p["friendly_name"], "members": set()}
            self._next_id += 1
        elif path == "group/remove":
            gid = self._gid(p["id"])
            if gid is None:
                return self._respond(path, tx, "error", "no such group")
            self.groups.pop(gid)
        elif path == "group/members/add":
            gid = self._gid(p["group"])
            if gid is None:
                return self._respond(path, tx, "error", "group does not exist yet")
            self.groups[gid]["members"].add(p["device"])
        elif path in ("device/bind", "device/unbind"):
            gid = self._gid(p["to"])
            if gid is None:
                return self._respond(path, tx, "error", "no such group")
            ep = p["from_endpoint"]
            (self.binds.setdefault(ep, set()).add if path == "device/bind" else self.binds.setdefault(ep, set()).discard)(gid)
        elif path == "converter/save":
            pass
        self._snapshots()
        self._respond(path, tx)

    def _set_slot(self, s):
        idx, serial, mask = s["index"], s["serial"], s["mask"]
        if serial == "00000000":
            self.slots.pop(idx, None)
        elif not mask or any(i != idx and v[0] == serial and v[1] & mask for i, v in self.slots.items()):
            return  # the C6 refuses it (invalid value), nothing changes
        else:
            self.slots[idx] = (serial, mask)

    def _slot_state(self):
        st = {"armed": True}
        for i in range(24):
            serial, mask = self.slots.get(i, ("00000000", 0))
            st[f"slot_{i}"] = f"{serial}:{mask:02x}"
        return st
