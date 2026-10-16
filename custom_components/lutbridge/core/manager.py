# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Applies a plan through Zigbee2MQTT's MQTT API and keeps a view of the live state.

Home Assistant is hidden behind a tiny MQTT protocol (`publish`, `subscribe`) so this runs unchanged in tests
against a fake Zigbee2MQTT. Requests to <base>/bridge/request/... carry a `transaction` id that Zigbee2MQTT echoes
in its response, which is how each step waits for the previous one (a group must exist before members are added).
Slot writes go through the device's /set topic, which has no response, so they are verified by reading back.
"""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from . import planner, z2m


class Mqtt(Protocol):
    async def publish(self, topic: str, payload: Any) -> None: ...

    async def subscribe(self, topic: str, callback: Callable[[str, Any], None]) -> Callable[[], None]:
        """Call callback(topic, decoded JSON payload) for matching messages. Returns an unsubscribe function."""


class ApplyError(Exception):
    def __init__(self, action: Any, message: str):
        super().__init__(f"{action}: {message}")
        self.action = action


@dataclass
class Report:
    done: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class Orchestrator:
    def __init__(self, mqtt: Mqtt, base: str = z2m.DEFAULT_BASE, timeout: float = 15.0):
        self.mqtt = mqtt
        self.base = base
        self.timeout = timeout
        self.devices: list[dict] = []
        self.groups: list[dict] = []
        self.state: dict = {}
        self.bridge: str | None = None
        self.bridge_name: str | None = None  # friendly name, which names the state and availability topics
        self.z2m_online: bool | None = None  # <base>/bridge/state; None until seen
        self.board_online: bool | None = None  # the board's availability topic; None when Zigbee2MQTT availability is off
        self.claim_slots_unsupported = False  # the board's firmware refused a per-slot claim (it predates them)
        self._pending: dict[str, asyncio.Future] = {}
        self._tx = itertools.count(1)
        self._unsub: list[Callable[[], None]] = []
        self._state_unsubs: list[Callable[[], None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- lifecycle ---------------------------------------------------------------

    def _hop(self, handler: Callable[[str, Any], None]) -> Callable[[str, Any], None]:
        """Wrap an MQTT handler so it always runs on the event loop, whichever thread the broker callback arrives on.
        Home Assistant runs a plain (non-@callback) MQTT callback in a worker thread, where asyncio objects such as
        futures and tasks must not be touched."""
        def wrapper(topic: str, payload: Any) -> None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                self._loop.call_soon_threadsafe(handler, topic, payload)
            else:
                handler(topic, payload)
        return wrapper

    async def start(self) -> None:
        b = self.base
        self._loop = asyncio.get_running_loop()
        self._unsub = [
            await self.mqtt.subscribe(f"{b}/bridge/response/#", self._hop(self._on_response)),
            await self.mqtt.subscribe(f"{b}/bridge/groups", self._hop(self._on_groups)),
            await self.mqtt.subscribe(f"{b}/bridge/devices", self._hop(self._on_devices)),
            await self.mqtt.subscribe(f"{b}/bridge/state", self._hop(self._on_z2m_state)),
            await self.mqtt.subscribe(f"{b}/bridge/logging", self._hop(self._on_z2m_log)),
        ]

    def stop(self) -> None:
        for u in self._unsub + self._state_unsubs:
            u()
        self._unsub, self._state_unsubs = [], []

    # ---- snapshots ---------------------------------------------------------------

    def _on_devices(self, topic: str, payload: Any) -> None:
        self.devices = payload or []
        bridge, name = z2m.find_bridge(self.devices), z2m.find_bridge_name(self.devices)
        if bridge != self.bridge or name != self.bridge_name:
            self.bridge, self.bridge_name = bridge, name
            self.board_online = None  # belongs to the old name; the new availability topic will say
            for u in self._state_unsubs:
                u()
            self._state_unsubs = []
            if name:
                asyncio.get_running_loop().create_task(self._track_state(name))

    async def _track_state(self, name: str) -> None:
        subs = [
            await self.mqtt.subscribe(f"{self.base}/{name}", self._hop(self._on_state)),
            await self.mqtt.subscribe(f"{self.base}/{name}/availability", self._hop(self._on_availability)),
        ]
        if name == self.bridge_name:
            self._state_unsubs = subs
        else:
            for u in subs:
                u()

    def _on_z2m_log(self, topic: str, payload: Any) -> None:
        if z2m.is_claim_slots_rejection(payload):
            self.claim_slots_unsupported = True

    def _on_z2m_state(self, topic: str, payload: Any) -> None:
        self.z2m_online = z2m.parse_online(payload)

    def _on_availability(self, topic: str, payload: Any) -> None:
        self.board_online = z2m.parse_online(payload)

    def _on_groups(self, topic: str, payload: Any) -> None:
        self.groups = payload or []

    def _on_state(self, topic: str, payload: Any) -> None:
        if isinstance(payload, dict):
            self.state.update(payload)

    def _on_response(self, topic: str, payload: Any) -> None:
        fut = self._pending.pop((payload or {}).get("transaction"), None)
        if fut and not fut.done():
            fut.set_result(payload)

    def current(self) -> planner.Current:
        by_name, by_id = z2m.parse_groups(self.groups)
        binds = z2m.parse_binds(self.devices, self.bridge, by_id) if self.bridge else {}
        slots = z2m.parse_slots(self.state)
        known = any(k.startswith("slot_") for k in self.state)
        return planner.Current(by_name, binds, slots, slots_known=known)

    async def refresh_slots(self, wait: float = 3.0, poll: float = 0.3) -> bool:
        """Ask the C6 for its slots; True if a read-back arrived in time."""
        if not self.bridge:
            return False
        for k in [k for k in self.state if k.startswith("slot_")]:
            del self.state[k]
        topic, body = z2m.read_slots(self.bridge, self.base)
        await self.mqtt.publish(topic, body)
        deadline = asyncio.get_running_loop().time() + wait
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(poll)
            if any(k.startswith("slot_") for k in self.state):
                return True
        return False

    # ---- requests ----------------------------------------------------------------

    async def request(self, path: str, payload: dict) -> dict:
        tx = f"lb{next(self._tx)}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[tx] = fut
        topic, body = z2m.request(self.base, path, {**payload, "transaction": tx})
        await self.mqtt.publish(topic, body)
        try:
            return await asyncio.wait_for(fut, self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(tx, None)
            raise

    async def claim(self, counter: int, handled=None) -> None:
        """Heartbeat. `handled` are the slot indexes Home Assistant is looking after; the rest arm on their own."""
        if self.bridge:
            topic, body = z2m.claim(self.bridge, counter, self.base, handled)
            await self.mqtt.publish(topic, body)

    async def install_converter(self, code: str) -> None:
        topic, body = z2m.install_converter(self.base, code)
        resp = await self.request(topic.removeprefix(f"{self.base}/bridge/request/"), body)
        if resp.get("status") != "ok":
            raise ApplyError("install converter", str(resp.get("error")))

    # ---- applying a plan ---------------------------------------------------------

    async def apply(self, plan: planner.Plan, pace: float = 0.4) -> Report:
        """Carry out the plan in order. Stops at the first failure, leaving earlier steps in place."""
        if not self.bridge:
            raise ApplyError("apply", "the pico bridge is not known to Zigbee2MQTT")
        report = Report()
        for action in plan.actions:
            topic, body = z2m.to_request(action, self.bridge, self.base)
            if topic.startswith(f"{self.base}/bridge/request/"):
                path = topic.removeprefix(f"{self.base}/bridge/request/")
                try:
                    resp = await self.request(path, body)
                except asyncio.TimeoutError:
                    raise ApplyError(action, "no response from Zigbee2MQTT") from None
                if resp.get("status") != "ok":
                    raise ApplyError(action, str(resp.get("error", resp)))
            else:  # device /set: no response exists, so pace it and verify afterwards
                await self.mqtt.publish(topic, body)
                await asyncio.sleep(pace)
            report.done.append(action)
        report.warnings = list(plan.warnings)
        return report

    async def verify_slots(self, want: dict, wait: float = 6.0, poll: float = 0.5) -> list[str]:
        """Read the slots back until they match `want` (index -> (serial, mask)) or time runs out.
        Returns a list of differences; empty means the C6 holds exactly the wanted slots."""
        if not self.bridge:
            return ["bridge unknown"]
        deadline = asyncio.get_running_loop().time() + wait
        diff: list[str] = []
        while True:
            for k in [k for k in self.state if k.startswith("slot_")]:
                del self.state[k]  # drop stale values so only the fresh read counts
            topic, body = z2m.read_slots(self.bridge, self.base)
            await self.mqtt.publish(topic, body)
            await asyncio.sleep(poll)
            have = z2m.parse_slots(self.state)
            diff = [f"slot {i}: have {have.get(i)}, want {want.get(i)}" for i in sorted(set(have) | set(want)) if have.get(i) != want.get(i)]
            if not diff or asyncio.get_running_loop().time() >= deadline:
                return diff
