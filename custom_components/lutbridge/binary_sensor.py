# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import LutBridgeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([HeartbeatOk(entry.runtime_data), Problem(entry.runtime_data)])


class HeartbeatOk(LutBridgeEntity, BinarySensorEntity):
    """On while claims are being sent, so the C6 stays out of the way for the Picos Home Assistant is handling. Off means
    the fallback is armed for every Pico."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, rt) -> None:
        super().__init__(rt, "heartbeat_ok", "Heartbeat running")

    @property
    def is_on(self) -> bool:
        return self.runtime.heartbeat.startswith("sending")


class Problem(LutBridgeEntity, BinarySensorEntity):
    """On when something stops the fallback being relied on: the board is offline, the heartbeat stopped, the mapping is
    out of date, or a Pico is being served by the fallback. The attributes say what; Repairs shows the same."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, rt) -> None:
        super().__init__(rt, "problem", "Problem")

    @property
    def is_on(self) -> bool:
        return not self.runtime.health.ok

    @property
    def extra_state_attributes(self) -> dict:
        h = self.runtime.health
        return {"problems": list(h.problems.values()), "keys": list(h.problems), "board": h.board}
