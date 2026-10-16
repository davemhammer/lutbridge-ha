# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Is the fallback in a state you can rely on? Pure Python: plain values in, a verdict out.

Each problem has a stable key (used for Repairs issues and automations) and a sentence for people. Things that are
merely unknown, such as the board's availability when Zigbee2MQTT availability is off, are never reported as fine
or as a problem; they are carried separately so a dashboard can show them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HEARTBEAT_STALE_SECONDS = 45  # three missed 15 s heartbeats, the bridge's own limit


@dataclass(frozen=True)
class Inputs:
    bridge_known: bool                  # the pico bridge is in Zigbee2MQTT's device list
    z2m_online: bool | None             # Zigbee2MQTT's bridge/state; None = not seen
    board_online: bool | None           # the board's availability topic; None = Zigbee2MQTT availability is off
    heartbeat_ok: bool                  # claims are being sent for at least some Picos
    claim_age_s: float | None           # seconds since the last claim was sent; None = never
    converter_current: bool | None      # the bundled converter is the one installed; None = unknown yet
    changes_pending: int | None         # operations a new plan would need; None = not computed yet
    on_fallback: dict = field(default_factory=dict)  # Pico name -> reasons it is left to the fallback
    firmware_supports_slots: bool = True  # False once the board has refused a per-Pico claim


@dataclass(frozen=True)
class Health:
    problems: dict                      # key -> sentence
    board: str                          # "online" | "offline" | "unknown"

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def summary(self) -> str:
        if self.ok:
            return "ok"
        n = len(self.problems)
        return next(iter(self.problems.values())) if n == 1 else f"{n} problems: " + "; ".join(self.problems.values())


def evaluate(i: Inputs) -> Health:
    p: dict = {}
    if not i.bridge_known:
        p["board_not_found"] = "The pico bridge is not paired with Zigbee2MQTT, so there is no fallback."
    if i.z2m_online is False:
        p["z2m_offline"] = "Zigbee2MQTT reports that it is offline; settings cannot be applied and claims cannot reach the board."
    if i.board_online is False:
        p["board_offline"] = "Zigbee2MQTT reports the pico bridge as offline, so Pico presses are not covered."
    if i.bridge_known and not i.heartbeat_ok:
        p["heartbeat"] = "The heartbeat is not being sent, so the fallback is armed for every Pico."
    elif i.claim_age_s is not None and i.claim_age_s > HEARTBEAT_STALE_SECONDS:
        p["heartbeat"] = f"The last claim was sent {int(i.claim_age_s)} s ago; the fallback has armed itself."
    if i.converter_current is False:
        p["converter"] = "Zigbee2MQTT is not running the converter bundled with this version; use the Install converter service."
    if i.changes_pending:
        p["drift"] = (f"The mapping is out of date: {i.changes_pending} change(s) are pending. "
                      "Make a plan and apply it, or press Configure.")
    if not i.firmware_supports_slots:
        p["firmware"] = ("The board's firmware does not support per-Pico claims (it needs lutbridge-esp32 0.2 or later), so a "
                         "broken automation arms the fallback for every Pico. Update the firmware.")
    if i.on_fallback:
        who = "; ".join(f"{name} ({', '.join(r)})" for name, r in sorted(i.on_fallback.items()))
        p["on_fallback"] = f"These Picos are being served by the fallback, not Home Assistant: {who}."
    board = "unknown" if i.board_online is None else "online" if i.board_online else "offline"
    return Health(p, board)
