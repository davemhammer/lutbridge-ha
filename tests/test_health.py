# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
from core.health import HEARTBEAT_STALE_SECONDS, Inputs, evaluate

GOOD = dict(bridge_known=True, z2m_online=True, board_online=True, heartbeat_ok=True, claim_age_s=5.0,
            converter_current=True, changes_pending=0, on_fallback={})


def health(**over):
    return evaluate(Inputs(**{**GOOD, **over}))


def test_everything_healthy_reports_ok():
    h = health()
    assert h.ok and h.problems == {} and h.summary == "ok" and h.board == "online"


def test_unknown_board_availability_is_neither_a_problem_nor_reported_online():
    h = health(board_online=None)
    assert h.ok and h.board == "unknown"


def test_offline_board_is_a_problem():
    h = health(board_online=False)
    assert list(h.problems) == ["board_offline"] and h.board == "offline" and not h.ok


def test_bridge_missing_from_zigbee2mqtt_is_reported_once_not_as_a_dead_heartbeat_too():
    h = health(bridge_known=False, heartbeat_ok=False, claim_age_s=None)
    assert list(h.problems) == ["board_not_found"]


def test_zigbee2mqtt_offline_is_reported():
    assert list(health(z2m_online=False).problems) == ["z2m_offline"]
    assert health(z2m_online=None).ok  # not seen yet is not the same as offline


def test_heartbeat_stopped_or_gone_stale_is_a_problem():
    assert list(health(heartbeat_ok=False).problems) == ["heartbeat"]
    assert health(claim_age_s=HEARTBEAT_STALE_SECONDS).ok
    stale = health(claim_age_s=HEARTBEAT_STALE_SECONDS + 1)
    assert list(stale.problems) == ["heartbeat"] and "armed itself" in stale.problems["heartbeat"]
    assert health(claim_age_s=None).ok  # no claim yet, e.g. just started


def test_old_converter_is_a_problem_but_unknown_is_not():
    assert list(health(converter_current=False).problems) == ["converter"]
    assert health(converter_current=None).ok


def test_pending_changes_mean_the_mapping_is_out_of_date():
    h = health(changes_pending=3)
    assert list(h.problems) == ["drift"] and "3 change(s)" in h.problems["drift"]
    assert health(changes_pending=None).ok  # not computed yet


def test_picos_on_fallback_are_named_with_the_reason():
    h = health(on_fallback={"Living Room": ["automation 'Living' is off"]})
    assert list(h.problems) == ["on_fallback"]
    assert "Living Room (automation 'Living' is off)" in h.problems["on_fallback"]


def test_firmware_without_per_pico_claims_is_a_problem():
    h = health(firmware_supports_slots=False)
    assert list(h.problems) == ["firmware"] and "lutbridge-esp32" in h.problems["firmware"]


def test_several_problems_are_all_listed_and_summarised():
    h = health(board_online=False, changes_pending=2)
    assert set(h.problems) == {"board_offline", "drift"} and h.summary.startswith("2 problems: ")
