# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from core import resolve

# light.kitchen_fan_lights is a light group of three Zigbee bulbs; light.all wraps it and a Caseta dimmer.
GROUPS = {
    "light.kitchen_fan_lights": ["light.kitchen_fan_1", "light.kitchen_fan_2", "light.kitchen_fan_3"],
    "light.all": ["light.kitchen_fan_lights", "light.bar_caseta"],
    "light.loop_a": ["light.loop_b"],
    "light.loop_b": ["light.loop_a", "light.kitchen_fan_1"],
}
Z2M = {f"light.kitchen_fan_{i}": frozenset({f"0x00{i}"}) for i in (1, 2, 3)}


def members_of(e):
    return GROUPS.get(e)


def z2m_of(e):
    return Z2M.get(e)


def test_group_expands_to_zigbee_devices():
    devices, unresolved = resolve.resolve(["light.kitchen_fan_lights"], members_of, z2m_of)
    assert devices == frozenset({"0x001", "0x002", "0x003"}) and not unresolved


def test_nested_groups_and_non_zigbee_members_are_reported():
    devices, unresolved = resolve.resolve(["light.all"], members_of, z2m_of)
    assert devices == frozenset({"0x001", "0x002", "0x003"})
    assert unresolved == {"light.bar_caseta"}


def test_group_cycles_do_not_hang():
    devices, unresolved = resolve.resolve(["light.loop_a"], members_of, z2m_of)
    assert devices == frozenset({"0x001"}) and not unresolved


def test_a_zigbee2mqtt_group_entity_stands_for_all_its_members():
    z2m = lambda e: frozenset({"0x001", "0x002"}) if e == "light.z2m_group" else None
    devices, _ = resolve.resolve(["light.z2m_group"], lambda e: None, z2m)
    assert devices == frozenset({"0x001", "0x002"})
