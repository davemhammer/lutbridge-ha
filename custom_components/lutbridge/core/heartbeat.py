# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Which slots a heartbeat claims. Pure Python.

A claim tells the bridge "Home Assistant is handling these Picos, stay quiet for them". A Pico whose source
automation is missing or switched off is left out, so the bridge serves that Pico (and only that Pico) once its last
claim is 45 s old, while the rest stay with Home Assistant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

SLOT_COUNT = 24


@dataclass(frozen=True)
class ClaimPlan:
    handled: frozenset                                    # slot indexes to claim
    on_fallback: dict = field(default_factory=dict)       # Pico name -> reasons it is left to the fallback

    @property
    def withheld_all(self) -> bool:
        return not self.handled

    def describe(self) -> str:
        """For the heartbeat sensor: empty when every Pico is claimed."""
        return "; ".join(f"{name}: {', '.join(reasons)}" for name, reasons in sorted(self.on_fallback.items()))


def plan_claim(
    applied_slots: Mapping[int, tuple],
    sources_by_serial: Mapping[str, Iterable[str]],
    problems: Mapping[str, str],
    names: Mapping[str, str] | None = None,
    legacy_sources: Iterable[str] = (),
) -> ClaimPlan:
    """Decide which of the 24 slots to claim.

    applied_slots      slot index -> (serial, mask), the table as last applied
    sources_by_serial  Pico serial -> the automations its mapping was derived from
    problems           automation alias -> what is wrong with it right now (missing, off); healthy ones are absent
    names              Pico serial -> display name, for messages
    legacy_sources     automations of a mapping applied before sources were tracked per Pico: if the mapping has
                       no per-Pico record, any problem among them withholds every slot, as the first version did

    Slots that hold no Pico are always claimed. A claim never names a slot past the table.
    """
    names = names or {}
    per_pico = {s: sorted(set(a)) for s, a in sources_by_serial.items()}
    known = bool(per_pico) or not applied_slots
    legacy_bad = [a for a in sorted(set(legacy_sources)) if a in problems]

    if not applied_slots and legacy_bad:
        return ClaimPlan(frozenset(), {"all Picos": [problems[a] for a in legacy_bad]})  # the first version's rule

    handled = set(range(SLOT_COUNT))
    on_fallback: dict = {}
    for idx, (serial, _mask) in sorted(applied_slots.items()):
        if not 0 <= idx < SLOT_COUNT:
            continue
        if known:
            reasons = [problems[a] for a in per_pico.get(serial, []) if a in problems]
        else:
            reasons = [problems[a] for a in legacy_bad]
        if reasons:
            handled.discard(idx)
            on_fallback[names.get(serial, serial)] = reasons
    valid = [i for i in applied_slots if 0 <= i < SLOT_COUNT]
    if valid and not any(i in handled for i in valid):
        handled = set()  # every Pico is left to the fallback: there is nothing for Home Assistant to claim
    return ClaimPlan(frozenset(handled), on_fallback)
