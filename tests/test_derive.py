# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from core import derive, pico

KITCHEN_OVEN_RIGHT = "0daa9768122e8b4db39e190c103ed9e0"
LIVING_ROOM = "f886776ba1724186c8fb172911a07c12"
PICOS = {KITCHEN_OVEN_RIGHT, LIVING_ROOM, "aaaa"}

# Real configs from the house (ha_config_get_automation), unmodified.
PICO_KITCHEN_LIGHTS_ON = {
    "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen_fan_lights"}}],
    "alias": "Pico_Kitchen_Lights_On",
    "conditions": [],
    "description": "",
    "id": "1637464155584",
    "mode": "single",
    "triggers": [{"device_id": KITCHEN_OVEN_RIGHT, "domain": "lutron_caseta", "subtype": "on", "trigger": "device", "type": "release"}],
}
LIVING_ROOM_BLUEPRINT = {
    "alias": "Lutron Pico - Living Room Lights",
    "id": "1715536957845",
    "use_blueprint": {
        "input": {"entity": ["light.living_room_fan_lights"], "pico": LIVING_ROOM, "pico_1": LIVING_ROOM, "speed": 300},
        "path": "smartqasa/lutron_pico_5_light.yaml",
    },
}


def test_r1_device_trigger_on_button():
    intents, unmapped = derive.derive([PICO_KITCHEN_LIGHTS_ON], PICOS)
    t = intents[KITCHEN_OVEN_RIGHT].targets
    assert set(t) == {pico.ON}
    assert t[pico.ON].entities == {"light.kitchen_fan_lights"}
    assert t[pico.ON].automations == {"Pico_Kitchen_Lights_On"}
    assert not unmapped


def test_r2_smartqasa_blueprint_covers_four_buttons_not_favorite():
    intents, unmapped = derive.derive([LIVING_ROOM_BLUEPRINT], PICOS)
    t = intents[LIVING_ROOM].targets
    assert set(t) == {pico.ON, pico.OFF, pico.RAISE, pico.LOWER}
    assert all(x.entities == {"light.living_room_fan_lights"} and "blueprint" in x.flags for x in t.values())
    assert not unmapped


def test_buttons_of_one_pico_accumulate_across_automations():
    off = {**PICO_KITCHEN_LIGHTS_ON, "alias": "Off", "triggers": [{**PICO_KITCHEN_LIGHTS_ON["triggers"][0], "subtype": "off"}],
           "actions": [{"action": "light.turn_off", "target": {"entity_id": ["light.a", "light.b"]}}]}
    intents, _ = derive.derive([PICO_KITCHEN_LIGHTS_ON, off], PICOS)
    t = intents[KITCHEN_OVEN_RIGHT].targets
    assert set(t) == {pico.ON, pico.OFF}
    assert t[pico.OFF].entities == {"light.a", "light.b"}


def test_stop_subtype_is_the_favorite_button():
    a = {**PICO_KITCHEN_LIGHTS_ON, "triggers": [{**PICO_KITCHEN_LIGHTS_ON["triggers"][0], "subtype": "stop"}]}
    intents, _ = derive.derive([a], PICOS)
    assert set(intents[KITCHEN_OVEN_RIGHT].targets) == {pico.FAVORITE}


def test_conditions_and_non_light_actions_are_flagged_not_dropped():
    a = {**PICO_KITCHEN_LIGHTS_ON,
         "conditions": [{"condition": "sun", "after": "sunset"}],
         "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.x"}}, {"delay": 5},
                     {"action": "scene.turn_on", "target": {"entity_id": "scene.movie"}}]}
    intents, _ = derive.derive([a], PICOS)
    flags = intents[KITCHEN_OVEN_RIGHT].targets[pico.ON].flags
    assert {"conditions", "complex"} <= flags
    assert intents[KITCHEN_OVEN_RIGHT].targets[pico.ON].entities == {"light.x"}


def test_legacy_keys_and_platform_device_are_understood():
    a = {"alias": "legacy", "trigger": [{"platform": "device", "domain": "lutron_caseta", "device_id": KITCHEN_OVEN_RIGHT, "subtype": "on", "type": "press"}],
         "action": [{"service": "light.turn_on", "data": {"entity_id": "light.legacy"}}]}
    intents, _ = derive.derive([a], PICOS)
    assert intents[KITCHEN_OVEN_RIGHT].targets[pico.ON].entities == {"light.legacy"}


def test_unrelated_automations_are_ignored():
    other = {"alias": "pool", "triggers": [{"trigger": "time", "at": "06:00"}], "actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.pump"}}]}
    other_device = {**PICO_KITCHEN_LIGHTS_ON, "triggers": [{**PICO_KITCHEN_LIGHTS_ON["triggers"][0], "device_id": "not-a-pico"}]}
    intents, unmapped = derive.derive([other, other_device], PICOS)
    assert intents == {} and unmapped == []


def test_unknown_blueprint_and_actionless_trigger_are_reported():
    bp = {"alias": "odd", "use_blueprint": {"path": "someone/fancy.yaml", "input": {"pico": LIVING_ROOM, "entity": ["light.q"]}}}
    nothing = {**PICO_KITCHEN_LIGHTS_ON, "alias": "scene only", "actions": [{"action": "scene.turn_on", "target": {"entity_id": "scene.s"}}]}
    intents, unmapped = derive.derive([bp, nothing], PICOS)
    assert intents == {}
    reasons = {u.automation: u.reason for u in unmapped}
    assert "not understood" in reasons["odd"] and reasons["scene only"] == "no light actions"


def test_model_button_layouts_and_serials():
    assert pico.serial_hex(93694086) == "0595a886"  # Dining Room Pico, from HA's device identifier
    assert pico.buttons_for_model("PJ2-3BRL-GXX-X01 (Pico3ButtonRaiseLower)") == pico.ON | pico.OFF | pico.FAVORITE | pico.RAISE | pico.LOWER
    assert pico.buttons_for_model("PJ2-2B-GXX-X01 (Pico2Button)") == pico.ON | pico.OFF
    assert pico.buttons_for_model("something else") == pico.ALL
