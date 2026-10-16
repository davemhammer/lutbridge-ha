# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .entity import LutBridgeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    rt = entry.runtime_data
    async_add_entities([PlanSensor(rt), HeartbeatSensor(rt), LastClaimSensor(rt), ProvisionedSensor(rt), HealthSensor(rt),
                        OnFallbackSensor(rt)])


class PlanSensor(LutBridgeEntity, SensorEntity):
    def __init__(self, rt) -> None:
        super().__init__(rt, "plan", "Plan")

    @property
    def native_value(self) -> str:
        return self.runtime.status

    @property
    def extra_state_attributes(self) -> dict:
        b = self.runtime.build
        if b is None:
            return {}
        # `review` is written for people (show it in a Markdown card); `summary` is the raw list of operations.
        return {"plan_id": b.plan_id, "review": b.review(self.runtime.light_names()), "summary": b.summary(), "issues": b.issues}


class HeartbeatSensor(LutBridgeEntity, SensorEntity):
    def __init__(self, rt) -> None:
        super().__init__(rt, "heartbeat", "Heartbeat")

    @property
    def native_value(self) -> str:
        return self.runtime.heartbeat[:255]


class LastClaimSensor(LutBridgeEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, rt) -> None:
        super().__init__(rt, "last_claim", "Last claim sent")

    @property
    def native_value(self):
        return self.runtime.last_claim


class ProvisionedSensor(LutBridgeEntity, SensorEntity):
    def __init__(self, rt) -> None:
        super().__init__(rt, "provisioned", "Picos provisioned")

    @property
    def native_value(self) -> int:
        return self.runtime.picos_provisioned


class HealthSensor(LutBridgeEntity, SensorEntity):
    """`ok`, or what is wrong. `board` says whether Zigbee2MQTT reports the pico bridge online ("unknown" when its
    availability feature is off)."""

    def __init__(self, rt) -> None:
        super().__init__(rt, "health", "Health")

    @property
    def native_value(self) -> str:
        return self.runtime.health.summary[:255]

    @property
    def extra_state_attributes(self) -> dict:
        h = self.runtime.health
        return {"problems": h.problems, "board": h.board, "changes_pending": self.runtime.changes_pending}


class OnFallbackSensor(LutBridgeEntity, SensorEntity):
    """How many Picos are being served by the fallback right now because their Home Assistant automation is missing
    or off. Normally 0."""

    def __init__(self, rt) -> None:
        super().__init__(rt, "on_fallback", "Picos on fallback")

    @property
    def native_value(self) -> int:
        return len(self.runtime.on_fallback)

    @property
    def extra_state_attributes(self) -> dict:
        return {"picos": {name: list(reasons) for name, reasons in sorted(self.runtime.on_fallback.items())}}
