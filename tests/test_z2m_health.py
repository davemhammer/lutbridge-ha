# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from core import z2m

BRIDGE = "0x58e6c5fffe191384"


def test_a_claim_can_name_the_slots_it_covers():
    assert z2m.claim(BRIDGE, 7, handled={3, 1, 2}) == (f"zigbee2mqtt/{BRIDGE}/set", {"claim": {"counter": 7, "slots": [1, 2, 3]}})
    assert z2m.claim(BRIDGE, 300, handled=[])[1] == {"claim": {"counter": 44, "slots": []}}
    assert z2m.claim(BRIDGE, 1, "other")[0] == f"other/{BRIDGE}/set"  # base topic stays the third argument
    assert z2m.claim(BRIDGE, 300)[1] == {"claim": 44}  # no slot list: the original form that claims every slot
    # naming every slot is the same thing, so it is sent as the plain claim that every firmware version understands
    assert z2m.claim(BRIDGE, 9, handled=set(range(24)))[1] == {"claim": 9}
    assert z2m.claim(BRIDGE, 9, handled=set(range(24)) - {6})[1] == {"claim": {"counter": 9, "slots": [s for s in range(24) if s != 6]}}


def test_online_state_is_read_from_both_message_shapes():
    assert z2m.parse_online({"state": "online"}) is True and z2m.parse_online("online") is True
    assert z2m.parse_online({"state": "offline"}) is False and z2m.parse_online("offline") is False
    assert z2m.parse_online(None) is None and z2m.parse_online({"state": "starting"}) is None


def test_a_refused_per_slot_claim_is_recognised_in_zigbee2mqtts_log():
    msg = "Publish 'set' 'claim' to '0x58e6c5fffe191384' failed: 'Error: Status 'UNSUP_COMMAND' undefined:claimSlots'"
    assert z2m.is_claim_slots_rejection({"level": "error", "message": msg, "namespace": "z2m"})
    assert not z2m.is_claim_slots_rejection({"level": "error", "message": msg.replace("claimSlots", "setSlot")})
    assert not z2m.is_claim_slots_rejection({"level": "info", "message": "MQTT publish: topic 'zigbee2mqtt/x', payload '{}'"})
    assert not z2m.is_claim_slots_rejection("a plain string") and not z2m.is_claim_slots_rejection(None)


def test_bridge_name_and_converter_status_come_from_the_device_list():
    devices = [{"ieee_address": "0x1", "friendly_name": "Lamp"},
               {"ieee_address": BRIDGE, "friendly_name": "Pico board", "model_id": "l-re.pico-bridge",
                "definition": {"source": "external", "model": "l-re.pico-bridge"}}]
    assert z2m.find_bridge_name(devices) == "Pico board" and z2m.find_bridge(devices) == BRIDGE
    assert z2m.bridge_has_current_converter(devices) is True
    devices[1]["definition"]["source"] = "generated"
    assert z2m.bridge_has_current_converter(devices) is False
    assert z2m.bridge_has_current_converter(devices[:1]) is None and z2m.find_bridge_name(devices[:1]) is None
    assert z2m.find_bridge_name([{"ieee_address": BRIDGE, "model_id": "l-re.pico-bridge"}]) == BRIDGE  # no friendly name yet
