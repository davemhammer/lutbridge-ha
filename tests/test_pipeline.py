# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from core import pico, pipeline, planner
from core.pipeline import PicoInfo
from test_derive import KITCHEN_OVEN_RIGHT, LIVING_ROOM, LIVING_ROOM_BLUEPRINT, PICO_KITCHEN_LIGHTS_ON

PICOS = {
    KITCHEN_OVEN_RIGHT: PicoInfo(KITCHEN_OVEN_RIGHT, "0c6be06a", "Kitchen Counter Oven Right", "Kitchen", "PJ2-3BRL-GXX-X01 (Pico3ButtonRaiseLower)"),
    LIVING_ROOM: PicoInfo(LIVING_ROOM, "059582be", "Living Room Pico", "Living Room", "PJ2-3BRL-GXX-X01 (Pico3ButtonRaiseLower)"),
}
GROUPS = {"light.kitchen_fan_lights": ["light.kitchen_fan_1", "light.kitchen_fan_2"],
          "light.living_room_fan_lights": ["light.lr_1", "light.lr_2", "light.lr_caseta"]}
Z2M = {"light.kitchen_fan_1": frozenset({"0xk1"}), "light.kitchen_fan_2": frozenset({"0xk2"}),
       "light.lr_1": frozenset({"0xl1"}), "light.lr_2": frozenset({"0xl2"})}


def run(autos, current=None, picos=PICOS):
    return pipeline.build(picos, autos, GROUPS.get, Z2M.get, current or planner.Current())


def test_real_automations_become_a_plan():
    b = run([PICO_KITCHEN_LIGHTS_ON, LIVING_ROOM_BLUEPRINT])
    assert {w.label for w in b.plan.slots.values()} == {"Kitchen Counter Oven Right", "Living Room Pico"}
    living = next(w for w in b.plan.slots.values() if w.label == "Living Room Pico")
    assert living.members == frozenset({"0xl1", "0xl2"})  # the Caseta bulb is not reachable by Zigbee
    assert living.mask == pico.ON | pico.OFF | pico.RAISE | pico.LOWER
    kitchen = next(w for w in b.plan.slots.values() if w.label == "Kitchen Counter Oven Right")
    assert kitchen.mask == pico.ON  # only the on button has an automation
    assert b.sources == {"Pico_Kitchen_Lights_On", "Lutron Pico - Living Room Lights"}


def test_issues_say_what_was_skipped_and_why():
    b = run([PICO_KITCHEN_LIGHTS_ON, LIVING_ROOM_BLUEPRINT])
    text = "\n".join(b.issues)
    assert "light.lr_caseta" in text and "not reachable by Zigbee" in text


def test_summary_is_readable_and_lists_every_change():
    s = run([LIVING_ROOM_BLUEPRINT]).summary()
    assert "slot 0 (endpoint 10): Living Room Pico [on+off+raise+lower] -> 2 light(s)" in s
    assert "create group lutbridge_living_room_pico" in s and "bind endpoint 10 to group lutbridge_living_room_pico" in s


def test_plan_id_is_stable_and_changes_with_the_plan():
    a = run([LIVING_ROOM_BLUEPRINT]).plan_id
    assert a == run([LIVING_ROOM_BLUEPRINT]).plan_id
    assert a != run([LIVING_ROOM_BLUEPRINT, PICO_KITCHEN_LIGHTS_ON]).plan_id


def test_a_pico_with_no_zigbee_lights_gets_no_slot():
    only_caseta = {**LIVING_ROOM_BLUEPRINT, "use_blueprint": {**LIVING_ROOM_BLUEPRINT["use_blueprint"], "input": {**LIVING_ROOM_BLUEPRINT["use_blueprint"]["input"], "entity": ["light.lr_caseta"]}}}
    b = run([only_caseta])
    assert b.plan.slots == {} and any("none of" in i for i in b.issues)


def test_buttons_the_model_does_not_have_are_dropped():
    two = {LIVING_ROOM: PicoInfo(LIVING_ROOM, "059582be", "Living Room Pico", None, "PJ2-2B-GXX-X01 (Pico2Button)")}
    b = run([LIVING_ROOM_BLUEPRINT], picos=two)
    assert next(iter(b.plan.slots.values())).mask == pico.ON | pico.OFF


def test_a_converged_system_has_an_empty_plan():
    first = run([LIVING_ROOM_BLUEPRINT])
    cur = planner.Current(
        groups={a.name: frozenset() for a in first.plan.actions if isinstance(a, planner.CreateGroup)},
        binds={a.endpoint: {a.group} for a in first.plan.actions if isinstance(a, planner.Bind)},
        slots={a.index: (a.serial, a.mask) for a in first.plan.actions if isinstance(a, planner.SetSlot)})
    cur.groups = {n: frozenset({m.ieee for m in first.plan.actions if isinstance(m, planner.AddMember) and m.group == n}) for n in cur.groups}
    assert run([LIVING_ROOM_BLUEPRINT], cur).plan.empty
