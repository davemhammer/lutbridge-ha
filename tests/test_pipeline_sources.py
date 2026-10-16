# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from test_derive import LIVING_ROOM_BLUEPRINT, PICO_KITCHEN_LIGHTS_ON
from test_pipeline import run


def test_each_pico_remembers_which_automations_it_came_from():
    b = run([PICO_KITCHEN_LIGHTS_ON, LIVING_ROOM_BLUEPRINT])
    assert b.sources_by_serial == {"0c6be06a": {"Pico_Kitchen_Lights_On"}, "059582be": {"Lutron Pico - Living Room Lights"}}
    assert set().union(*b.sources_by_serial.values()) == b.sources


def test_an_uncovered_pico_has_no_sources():
    bp = LIVING_ROOM_BLUEPRINT["use_blueprint"]
    only_caseta = {**LIVING_ROOM_BLUEPRINT, "use_blueprint": {**bp, "input": {**bp["input"], "entity": ["light.lr_caseta"]}}}
    b = run([only_caseta])
    assert b.sources_by_serial == {} and b.sources == set()
