# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from core import planner, z2m

BRIDGE = "0x58e6c5fffe191384"


def test_requests_match_the_zigbee2mqtt_bridge_api_used_on_the_bench():
    P = planner
    assert z2m.to_request(P.CreateGroup("lutbridge_x"), BRIDGE) == ("zigbee2mqtt/bridge/request/group/add", {"friendly_name": "lutbridge_x"})
    assert z2m.to_request(P.AddMember("g", "0x1"), BRIDGE) == ("zigbee2mqtt/bridge/request/group/members/add", {"group": "g", "device": "0x1"})
    assert z2m.to_request(P.RemoveGroup("g"), BRIDGE) == ("zigbee2mqtt/bridge/request/group/remove", {"id": "g"})
    topic, payload = z2m.to_request(P.Bind(10, "g"), BRIDGE)
    assert topic.endswith("device/bind") and payload == {"from": BRIDGE, "from_endpoint": 10, "to": "g", "clusters": ["genOnOff", "genLevelCtrl"]}
    assert z2m.to_request(P.Unbind(10, "g"), BRIDGE)[0].endswith("device/unbind")


def test_slot_and_claim_requests():
    assert z2m.to_request(planner.SetSlot(3, "0595a886", 15), BRIDGE) == (f"zigbee2mqtt/{BRIDGE}/set", {"slot": {"index": 3, "serial": "0595a886", "mask": 15}})
    assert z2m.to_request(planner.ClearSlot(3), BRIDGE)[1] == {"slot": {"index": 3, "serial": "00000000", "mask": 0}}
    assert z2m.claim(BRIDGE, 300) == (f"zigbee2mqtt/{BRIDGE}/set", {"claim": 44})  # wraps to a byte
    assert z2m.read_slots(BRIDGE) == (f"zigbee2mqtt/{BRIDGE}/get", {"slots": ""})


def test_custom_base_topic():
    assert z2m.to_request(planner.CreateGroup("g"), BRIDGE, base="z2m")[0] == "z2m/bridge/request/group/add"


def test_converter_install_request():
    topic, payload = z2m.install_converter("zigbee2mqtt", "export default {}")
    assert topic == "zigbee2mqtt/bridge/request/converter/save" and payload == {"name": "pico_bridge.mjs", "code": "export default {}"}


def test_parse_groups_and_binds_from_retained_snapshots():
    groups = [{"id": 10, "friendly_name": "lutbridge_test", "members": [{"ieee_address": "0x1", "endpoint": 11}]},
              {"id": 3, "friendly_name": "kitchen", "members": []}]
    by_name, by_id = z2m.parse_groups(groups)
    assert by_name == {"lutbridge_test": frozenset({"0x1"}), "kitchen": frozenset()} and by_id == {10: "lutbridge_test", 3: "kitchen"}
    devices = [
        {"ieee_address": "0xother", "endpoints": {"1": {"bindings": [{"cluster": "genOnOff", "target": {"type": "group", "id": 3}}]}}},
        {"ieee_address": BRIDGE, "model_id": "l-re.pico-bridge", "endpoints": {
            "1": {"bindings": []},
            "10": {"bindings": [{"cluster": "genOnOff", "target": {"type": "group", "id": 10}},
                                {"cluster": "genLevelCtrl", "target": {"type": "group", "id": 10}}]},
            "11": {"bindings": [{"cluster": "genOnOff", "target": {"type": "endpoint", "ieee_address": "0x2", "endpoint": 1}}]},
        }},
    ]
    assert z2m.parse_binds(devices, BRIDGE, by_id) == {10: {"lutbridge_test"}}
    assert z2m.find_bridge(devices) == BRIDGE


def test_parse_slots_from_device_state():
    state = {"slot_0": "02dd0abf:1f", "slot_1": "00000000:00", "slot_5": "0595a886:0f", "armed": True}
    assert z2m.parse_slots(state) == {0: ("02dd0abf", 0x1F), 5: ("0595a886", 0x0F)}
