# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Adapts Home Assistant's MQTT integration to the small Mqtt protocol the orchestrator uses."""
from __future__ import annotations

import json
from typing import Any, Callable

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant
from homeassistant.core import callback as ha_callback  # aliased: `callback` is also the handler parameter below


class HaMqtt:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def publish(self, topic: str, payload: Any) -> None:
        await mqtt.async_publish(self.hass, topic, payload if isinstance(payload, str) else json.dumps(payload))

    async def subscribe(self, topic: str, handler: Callable[[str, Any], None]) -> Callable[[], None]:
        @ha_callback  # without this Home Assistant runs the function in a worker thread instead of on the event loop
        def _on_message(msg) -> None:
            raw = msg.payload
            try:
                data = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                data = raw  # Zigbee2MQTT publishes a few plain-text topics; pass them through untouched
            handler(msg.topic, data)

        return await mqtt.async_subscribe(self.hass, topic, _on_message)
