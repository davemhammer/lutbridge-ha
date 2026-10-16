# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import LutBridgeEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    rt = entry.runtime_data
    async_add_entities([PlanButton(rt), ApplyButton(rt)])


class PlanButton(LutBridgeEntity, ButtonEntity):
    def __init__(self, rt) -> None:
        super().__init__(rt, "make_plan", "Make plan")

    async def async_press(self) -> None:
        await self.runtime.async_plan()


class ApplyButton(LutBridgeEntity, ButtonEntity):
    """Pressing this is the approval: it applies the plan that was last shown, if nothing has changed since."""

    def __init__(self, rt) -> None:
        super().__init__(rt, "apply_plan", "Apply plan")

    async def async_press(self) -> None:
        await self.runtime.async_apply()
