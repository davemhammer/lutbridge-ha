# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from core.heartbeat import SLOT_COUNT, plan_claim

ALL = frozenset(range(SLOT_COUNT))
APPLIED = {0: ("aaaa0001", 0x0F), 1: ("aaaa0002", 0x03), 2: ("aaaa0003", 0x0F)}
SOURCES = {"aaaa0001": {"Kitchen on", "Kitchen off"}, "aaaa0002": {"Hall"}, "aaaa0003": {"Living"}}
NAMES = {"aaaa0001": "Kitchen", "aaaa0002": "Hall", "aaaa0003": "Living Room"}


def claim(problems, **kw):
    return plan_claim(APPLIED, SOURCES, problems, NAMES, **kw)


def test_every_pico_is_claimed_when_all_automations_are_healthy():
    c = claim({})
    assert c.handled == ALL and c.on_fallback == {} and c.describe() == "" and not c.withheld_all


def test_only_the_pico_with_a_broken_automation_is_left_to_the_fallback():
    c = claim({"Living": "automation 'Living' is off"})
    assert c.handled == ALL - {2}
    assert c.on_fallback == {"Living Room": ["automation 'Living' is off"]}
    assert "Living Room: automation 'Living' is off" in c.describe()


def test_one_broken_automation_of_several_is_enough_to_leave_that_pico_out():
    c = claim({"Kitchen off": "automation 'Kitchen off' no longer exists"})
    assert c.handled == ALL - {0} and list(c.on_fallback) == ["Kitchen"]


def test_unused_slots_are_always_claimed():
    c = claim({"Hall": "automation 'Hall' is off"})
    assert set(range(3, SLOT_COUNT)) <= c.handled


def test_a_pico_split_over_two_slots_is_left_out_of_both():
    applied = {4: ("bbbb0001", 0x03), 5: ("bbbb0001", 0x0C), 6: ("bbbb0002", 0x0F)}
    sources = {"bbbb0001": {"Split"}, "bbbb0002": {"Other"}}
    c = plan_claim(applied, sources, {"Split": "automation 'Split' is off"}, {})
    assert c.handled == ALL - {4, 5} and list(c.on_fallback) == ["bbbb0001"]  # unnamed Picos show their serial


def test_when_every_pico_is_left_out_there_is_nothing_to_claim():
    c = claim({"Kitchen on": "x", "Hall": "y", "Living": "z"})
    assert c.withheld_all and c.handled == frozenset() and len(c.on_fallback) == 3


def test_nothing_applied_means_nothing_to_withhold():
    c = plan_claim({}, {}, {}, {})
    assert c.handled == ALL and not c.withheld_all


def test_a_mapping_applied_before_sources_were_tracked_per_pico_uses_the_old_all_or_nothing_rule():
    # The first version remembered one set of automations for the whole mapping and withheld everything.
    legacy = {"Old automation"}
    ok = plan_claim(APPLIED, {}, {}, NAMES, legacy_sources=legacy)
    assert ok.handled == ALL
    bad = plan_claim(APPLIED, {}, {"Old automation": "automation 'Old automation' is off"}, NAMES, legacy_sources=legacy)
    assert bad.withheld_all and set(bad.on_fallback) == {"Kitchen", "Hall", "Living Room"}
    none_applied = plan_claim({}, {}, {"Old automation": "automation 'Old automation' is off"}, NAMES, legacy_sources=legacy)
    assert none_applied.withheld_all and "all Picos" in none_applied.on_fallback


def test_a_slot_index_past_the_table_is_ignored():
    c = plan_claim({99: ("cccc0001", 1)}, {"cccc0001": {"X"}}, {"X": "bad"}, {})
    assert c.handled == ALL and c.on_fallback == {}
