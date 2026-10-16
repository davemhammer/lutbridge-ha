# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
import dataclasses

from core import pico, pipeline, planner, z2m
from test_derive import LIVING_ROOM, LIVING_ROOM_BLUEPRINT
from test_pipeline import GROUPS, PICOS, Z2M, run
from test_planner import apply

NAMES = {"0xl1": "Living Lamp 1", "0xl2": "Living Lamp 2"}


def converged():
    first = run([LIVING_ROOM_BLUEPRINT])
    return apply(first.plan, planner.Current()), first


def test_a_new_pico_reads_as_a_person_would_want_it():
    text = run([LIVING_ROOM_BLUEPRINT]).review(NAMES)
    assert "**Living Room Pico** (new)" in text
    assert "- on, off, raise, lower: Living Lamp 1, Living Lamp 2" in text
    assert "uses slot 0 (endpoint 10) and new group `lutbridge_living_room_pico`" in text
    assert "0x" not in text.split("Operations:")[0]  # no Zigbee addresses where names are known
    assert "Operations: 1 group(s) created, 2 light(s) added to groups, 1 bind(s), 1 slot write(s)" in text


def test_nothing_to_do_is_said_plainly():
    cur, _ = converged()
    text = run([LIVING_ROOM_BLUEPRINT], cur).review(NAMES)
    assert text.startswith("Everything is up to date: 1 Pico(s) covered.")
    assert "(unchanged)" in text and "Operations:" not in text


def test_a_changed_button_set_is_marked_changed_and_an_existing_group_is_called_existing():
    cur, _ = converged()
    cur = dataclasses.replace(cur, slots={0: ("059582be", pico.ON)})  # the C6 holds a narrower slot than wanted
    text = run([LIVING_ROOM_BLUEPRINT], cur).review(NAMES)
    assert "(changed)" in text and "existing group `lutbridge_living_room_pico`" in text


def test_a_pico_that_is_no_longer_wanted_is_listed_by_name():
    cur, _ = converged()
    text = run([], cur).review(NAMES)  # the automation is gone
    assert "**No longer covered**" in text and "- Living Room Pico" in text


def test_unknown_serials_fall_back_to_the_serial():
    cur = planner.Current(slots={3: ("deadbeef", pico.ON)})
    assert "- deadbeef" in run([], cur).review()


def test_notes_and_skipped_lights_are_shown_with_the_review():
    text = run([LIVING_ROOM_BLUEPRINT]).review(NAMES)
    assert "**Not covered / notes**" in text and "light.lr_caseta" in text


def test_light_names_default_to_the_address_when_unknown():
    assert "0xl1, 0xl2" in run([LIVING_ROOM_BLUEPRINT]).review()


# ---- slots we cannot read back ----------------------------------------------------------------------------------

def test_remembered_slots_make_a_converged_system_read_up_to_date():
    cur, _ = converged()
    unreadable = dataclasses.replace(cur, slots={}, slots_known=False)  # Zigbee2MQTT gave us no slot read-back
    remembered = {0: ("059582be", pico.ON | pico.OFF | pico.RAISE | pico.LOWER)}
    assert not run([LIVING_ROOM_BLUEPRINT], unreadable).plan.empty  # without memory it rewrites everything
    eff = pipeline.effective_current(unreadable, remembered)
    assert run([LIVING_ROOM_BLUEPRINT], eff).plan.empty


def test_a_live_read_always_beats_memory_and_force_ignores_both():
    live = planner.Current(slots={0: ("059582be", pico.ON)}, slots_known=True)
    assert pipeline.effective_current(live, {0: ("other", 1)}) is live
    forced = pipeline.effective_current(live, {0: ("other", 1)}, force=True)
    assert forced.slots == {} and forced.slots_known is False
    nothing = planner.Current(slots_known=False)
    assert pipeline.effective_current(nothing, {}) is nothing


def test_converter_hash_tracks_content():
    assert z2m.converter_hash("a") == z2m.converter_hash("a") != z2m.converter_hash("b")
