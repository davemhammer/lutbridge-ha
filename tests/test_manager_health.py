# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
import asyncio

from fake_z2m import FakeZ2M
from test_manager import ThreadedZ2M, make


def test_claim_names_the_slots_home_assistant_is_handling():
    async def go():
        fake, orch = await make()
        await orch.claim(5)
        await orch.claim(6, {0, 2})
        assert fake.claims == [5, {"counter": 6, "slots": [0, 2]}]
    asyncio.run(go())


def test_board_availability_and_zigbee2mqtt_state_are_tracked():
    async def go():
        fake, orch = await make()
        await asyncio.sleep(0.05)
        assert orch.z2m_online is True and orch.board_online is None  # availability is off until Zigbee2MQTT says otherwise
        fake.set_availability(True)
        assert orch.board_online is True
        fake.set_availability(False)
        fake.set_z2m_state(False)
        assert orch.board_online is False and orch.z2m_online is False
    asyncio.run(go())


def test_availability_published_before_we_subscribe_is_picked_up():
    async def go():
        fake = FakeZ2M()
        fake.availability = False  # retained on the broker
        _, orch = await make(fake)
        await asyncio.sleep(0.05)
        assert orch.board_online is False
    asyncio.run(go())


def test_availability_arrives_when_every_message_comes_from_a_worker_thread():
    async def go():
        fake, orch = await make(ThreadedZ2M())
        await asyncio.sleep(0.05)
        fake.set_availability(False)
        await asyncio.sleep(0.05)  # handlers hop onto the loop
        assert orch.board_online is False
    asyncio.run(go())


def test_the_firmware_refusing_a_per_slot_claim_is_noticed():
    async def go():
        fake, orch = await make()
        await asyncio.sleep(0.05)
        assert orch.claim_slots_unsupported is False
        fake.emit_log("MQTT publish: topic 'zigbee2mqtt/x', payload '{}'", "info")
        assert orch.claim_slots_unsupported is False
        fake.emit_log("Publish 'set' 'claim' to '0x58' failed: 'Error: Status 'UNSUP_COMMAND' undefined:claimSlots'")
        assert orch.claim_slots_unsupported is True
    asyncio.run(go())


def test_stopping_drops_every_subscription():
    async def go():
        fake, orch = await make()
        await asyncio.sleep(0.05)
        orch.stop()
        assert fake._subs == []
    asyncio.run(go())
