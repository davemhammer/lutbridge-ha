# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from core import pico
from core.planner import (AddMember, Bind, ClearSlot, Current, CreateGroup, PicoTargets, RemoveGroup, SetSlot, Unbind,
                          plan, wants_from, SLOT_COUNT)

FANS = frozenset({"0x1", "0x2", "0x3"})
LAMPS = frozenset({"0x4", "0x5"})
ALL4 = {pico.ON: FANS, pico.OFF: FANS, pico.RAISE: FANS, pico.LOWER: FANS}


def dining(members=FANS):
    return PicoTargets("0595a886", "Dining Room Pico", {b: members for b in (pico.ON, pico.OFF, pico.RAISE, pico.LOWER)})


def living():
    return PicoTargets("059582be", "Living Room Pico", {b: LAMPS for b in (pico.ON, pico.OFF, pico.RAISE, pico.LOWER)})


def apply(p, c: Current) -> Current:
    """Simulate carrying out a plan, to check that planning again afterwards changes nothing."""
    groups = {k: set(v) for k, v in c.groups.items()}
    binds = {k: set(v) for k, v in c.binds.items()}
    slots = dict(c.slots)
    for a in p.actions:
        if isinstance(a, Unbind):
            binds.get(a.endpoint, set()).discard(a.group)
        elif isinstance(a, ClearSlot):
            slots.pop(a.index, None)
        elif isinstance(a, RemoveGroup):
            groups.pop(a.name, None)
        elif isinstance(a, CreateGroup):
            groups[a.name] = set()
        elif isinstance(a, AddMember):
            groups[a.group].add(a.ieee)
        elif isinstance(a, SetSlot):
            slots[a.index] = (a.serial, a.mask)
        elif isinstance(a, Bind):
            binds.setdefault(a.endpoint, set()).add(a.group)
    return Current({k: frozenset(v) for k, v in groups.items()}, {k: v for k, v in binds.items() if v}, slots)


def test_one_endpoint_per_pico_when_all_buttons_reach_the_same_lights():
    w = wants_from([dining()])
    assert len(w) == 1 and w[0].mask == pico.ON | pico.OFF | pico.RAISE | pico.LOWER


def test_split_pico_gets_one_endpoint_per_distinct_target_set():
    p = PicoTargets("0595a886", "Dining", {pico.ON: FANS, pico.OFF: FANS, pico.RAISE: LAMPS, pico.LOWER: LAMPS})
    w = wants_from([p])
    assert {(x.mask, x.members) for x in w} == {(pico.ON | pico.OFF, FANS), (pico.RAISE | pico.LOWER, LAMPS)}


def test_fresh_plan_orders_actions_and_assigns_lowest_slots():
    p = plan(wants_from([dining(), living()]), Current())
    kinds = [type(a).__name__ for a in p.actions]
    # groups are built before slots are written, and slots before binds
    assert kinds.index("CreateGroup") < kinds.index("SetSlot") < kinds.index("Bind")
    slots = [a for a in p.actions if isinstance(a, SetSlot)]
    # slots are assigned in serial order, so the result does not depend on discovery order
    assert [(a.index, a.serial) for a in slots] == [(0, "059582be"), (1, "0595a886")]
    binds = {a.endpoint: a.group for a in p.actions if isinstance(a, Bind)}
    assert binds == {10: "lutbridge_living_room_pico", 11: "lutbridge_dining_room_pico"}
    assert {a.ieee for a in p.actions if isinstance(a, AddMember) and a.group == "lutbridge_dining_room_pico"} == set(FANS)


def test_planning_again_after_applying_is_empty():
    c = Current()
    p = plan(wants_from([dining(), living()]), c)
    assert plan(wants_from([dining(), living()]), apply(p, c)).empty


def test_adding_a_pico_keeps_existing_slots_and_takes_the_lowest_free_one():
    c = apply(plan(wants_from([dining(), living()]), Current()), Current())
    third = PicoTargets("02dd0abf", "Test Pico", {pico.ON: FANS})
    p = plan(wants_from([dining(), living(), third]), c)
    assert [type(a).__name__ for a in p.actions] == ["SetSlot", "Bind"]
    assert p.actions[0] == SetSlot(2, "02dd0abf", pico.ON)
    # same lights as Dining, so the existing group is reused instead of creating a duplicate
    assert p.actions[1] == Bind(12, "lutbridge_dining_room_pico")


def test_removing_a_pico_unbinds_clears_the_slot_and_drops_its_group():
    c = apply(plan(wants_from([dining(), living()]), Current()), Current())
    p = plan(wants_from([dining()]), c)
    assert p.actions == [Unbind(10, "lutbridge_living_room_pico"), ClearSlot(0), RemoveGroup("lutbridge_living_room_pico")]


def test_changed_targets_rebuild_the_group_and_rebind_in_a_safe_order():
    c = apply(plan(wants_from([dining()]), Current()), Current())
    p = plan(wants_from([dining(LAMPS)]), c)
    kinds = [type(a).__name__ for a in p.actions]
    assert kinds[0] == "Unbind" and kinds[-1] == "Bind"
    assert p.actions[0] == Unbind(10, "lutbridge_dining_room_pico")
    assert RemoveGroup("lutbridge_dining_room_pico") in p.actions
    assert plan(wants_from([dining(LAMPS)]), apply(p, c)).empty


def test_changed_button_mask_clears_the_old_slot_before_writing_the_new_one():
    full = dining()
    c = apply(plan(wants_from([full]), Current()), Current())
    fewer = PicoTargets("0595a886", "Dining Room Pico", {pico.ON: FANS, pico.OFF: FANS})
    p = plan(wants_from([fewer]), c)
    kinds = [type(a).__name__ for a in p.actions]
    assert kinds.index("ClearSlot") < kinds.index("SetSlot")  # the C6 refuses overlapping masks for one serial


def test_existing_foreign_group_with_the_same_members_is_reused():
    c = Current(groups={"kitchen_lights": FANS})
    p = plan(wants_from([dining()]), c)
    assert not any(isinstance(a, CreateGroup) for a in p.actions)
    assert p.actions[-1] == Bind(10, "kitchen_lights")


def test_groups_we_did_not_create_are_never_removed():
    c = Current(groups={"kitchen_lights": FANS, "bedroom": LAMPS})
    p = plan(wants_from([]), c)
    assert p.empty


def test_more_picos_than_endpoints_warns_instead_of_failing():
    picos = [PicoTargets(f"{i:08x}", f"Pico {i}", {pico.ON: frozenset({f"0x{i}"})}) for i in range(1, SLOT_COUNT + 3)]
    p = plan(wants_from(picos), Current())
    assert len(p.slots) == SLOT_COUNT and len(p.warnings) == 2


def test_existing_test_setup_is_replaced_cleanly():
    # The bench state: spare Pico in slot 0, endpoint 10 bound to lutbridge_test containing one bulb.
    c = Current(groups={"lutbridge_test": frozenset({"0xbulb"})}, binds={10: {"lutbridge_test"}}, slots={0: ("02dd0abf", 0x1F)})
    p = plan(wants_from([dining()]), c)
    assert p.actions[0] == Unbind(10, "lutbridge_test") and ClearSlot(0) in p.actions
    assert RemoveGroup("lutbridge_test") in p.actions


def test_unreadable_slots_are_all_cleared_first_then_rewritten():
    # The C6's slots could not be read back: assume nothing about them.
    c = Current(groups={}, binds={}, slots={}, slots_known=False)
    p = plan(wants_from([dining()]), c)
    sets = [a for a in p.actions if isinstance(a, SetSlot)]
    clears = [a.index for a in p.actions if isinstance(a, ClearSlot)]
    assert sets == [SetSlot(0, "0595a886", 0x0F)]
    assert clears == list(range(SLOT_COUNT))  # all cleared first, so no stale overlap can make the C6 refuse a write
    kinds = [type(a).__name__ for a in p.actions]
    assert max(i for i, k in enumerate(kinds) if k == "ClearSlot") < kinds.index("SetSlot")
