# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
import asyncio

import pytest
from fake_z2m import BASE, BRIDGE, FakeZ2M

from core import pico, planner
from core.manager import ApplyError, Orchestrator
from core.planner import PicoTargets, plan, wants_from

FANS = frozenset({"0x1", "0x2"})
LAMPS = frozenset({"0x3"})


def pico_targets(serial, name, members=FANS, buttons=(pico.ON, pico.OFF, pico.RAISE, pico.LOWER)):
    return PicoTargets(serial, name, {b: members for b in buttons})


async def make(fake=None, timeout=0.5):
    fake = fake or FakeZ2M()
    orch = Orchestrator(fake, BASE, timeout=timeout)
    await orch.start()
    await asyncio.sleep(0)  # let the state subscription for the bridge settle
    return fake, orch


async def converge(orch, picos):
    p = plan(wants_from(picos), orch.current())
    report = await orch.apply(p, pace=0)
    want = {i: (w.serial, w.mask) for i, w in p.slots.items()}
    return p, report, await orch.verify_slots(want, wait=0.2, poll=0.01)


class ThreadedZ2M(FakeZ2M):
    """Delivers every message from a worker thread, as Home Assistant does for an MQTT callback that is not marked
    @callback. Handlers that touch asyncio objects directly from such a thread fail with 'no running event loop'."""

    def _emit(self, topic, payload):
        import fnmatch
        import threading

        for pattern, cb in list(self._subs):
            if fnmatch.fnmatch(topic, pattern.replace("#", "*")):
                t = threading.Thread(target=cb, args=(topic, payload))
                t.start()
                t.join()

    def _replay(self, pattern, cb):
        import threading

        super()._replay(pattern, lambda *a: (lambda t: (t.start(), t.join()))(threading.Thread(target=cb, args=a)))


def test_messages_delivered_from_worker_threads_still_provision_everything():
    async def go():
        fake, orch = await make(ThreadedZ2M())
        await asyncio.sleep(0.05)  # the state subscription is created from the loop after the devices message
        picos = [pico_targets("0595a886", "Dining Room Pico"), pico_targets("059582be", "Living Room Pico", LAMPS)]
        _, _, diff = await converge(orch, picos)
        assert diff == [] and fake.slots == {0: ("059582be", 0x0F), 1: ("0595a886", 0x0F)}
        assert plan(wants_from(picos), orch.current()).empty
    asyncio.run(go())


def test_full_provisioning_then_nothing_left_to_do():
    async def go():
        fake, orch = await make()
        picos = [pico_targets("0595a886", "Dining Room Pico"), pico_targets("059582be", "Living Room Pico", LAMPS)]
        p, report, diff = await converge(orch, picos)
        assert diff == [] and len(report.done) == len(p.actions)
        assert fake.slots == {0: ("059582be", 0x0F), 1: ("0595a886", 0x0F)}
        assert {g["name"]: g["members"] for g in fake.groups.values()} == {
            "lutbridge_dining_room_pico": {"0x1", "0x2"}, "lutbridge_living_room_pico": {"0x3"}}
        # the live state, as Zigbee2MQTT now reports it, needs no further changes
        assert plan(wants_from(picos), orch.current()).empty
    asyncio.run(go())


def test_bench_setup_is_replaced_without_leaving_strays():
    async def go():
        fake = FakeZ2M()
        fake.groups[10] = {"name": "lutbridge_test", "members": {"0xbulb"}}
        fake.binds[10] = {10}
        fake.slots[0] = ("02dd0abf", 0x1F)
        _, orch = await make(fake)
        _, _, diff = await converge(orch, [pico_targets("0595a886", "Dining Room Pico")])
        assert diff == []
        assert "lutbridge_test" not in {g["name"] for g in fake.groups.values()}
        assert fake.slots == {0: ("0595a886", 0x0F)}
        assert fake.binds[10] == {fake._gid("lutbridge_dining_room_pico")}
    asyncio.run(go())


def test_narrowing_a_mask_works_even_though_the_c6_refuses_overlaps():
    async def go():
        fake, orch = await make()
        await converge(orch, [pico_targets("0595a886", "Dining Room Pico")])
        narrower = pico_targets("0595a886", "Dining Room Pico", buttons=(pico.ON, pico.OFF))
        _, _, diff = await converge(orch, [narrower])
        assert diff == [] and fake.slots == {0: ("0595a886", 0x03)}
    asyncio.run(go())


def test_failure_stops_at_the_failing_step_and_names_it():
    async def go():
        fake, orch = await make(FakeZ2M(fail_on="group/add"))
        p = plan(wants_from([pico_targets("0595a886", "Dining Room Pico")]), orch.current())
        with pytest.raises(ApplyError) as e:
            await orch.apply(p, pace=0)
        assert isinstance(e.value.action, planner.CreateGroup)
        assert fake.slots == {} and fake.binds == {}  # nothing after the failed step ran
    asyncio.run(go())


def test_no_response_from_zigbee2mqtt_is_reported_not_hung():
    class Silent(FakeZ2M):
        def _respond(self, *a, **k):
            pass
    async def go():
        _, orch = await make(Silent(), timeout=0.05)
        p = plan(wants_from([pico_targets("0595a886", "Dining Room Pico")]), orch.current())
        with pytest.raises(ApplyError, match="no response"):
            await orch.apply(p, pace=0)
    asyncio.run(go())


def test_verification_reports_a_slot_the_c6_refused():
    async def go():
        fake, orch = await make()
        p = plan(wants_from([pico_targets("0595a886", "Dining Room Pico")]), orch.current())
        await orch.apply(p, pace=0)
        fake.slots.clear()  # as if the SetSlot had been dropped on the air
        diff = await orch.verify_slots({0: ("0595a886", 0x0F)}, wait=0.1, poll=0.01)
        assert diff and "slot 0" in diff[0]
    asyncio.run(go())


def test_apply_refuses_when_the_bridge_is_not_paired():
    async def go():
        fake = FakeZ2M()
        fake.devices_payload = lambda: [{"ieee_address": "0x1", "endpoints": {}}]
        _, orch = await make(fake)
        with pytest.raises(ApplyError, match="not known"):
            await orch.apply(plan(wants_from([pico_targets("0595a886", "x")]), orch.current()), pace=0)
    asyncio.run(go())


def test_claim_goes_to_the_bridge_set_topic():
    async def go():
        fake, orch = await make()
        await orch.claim(5)
        assert (f"{BASE}/{BRIDGE}/set", {"claim": 5}) in fake.published
    asyncio.run(go())
