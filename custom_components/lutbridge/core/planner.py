# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Turn the wanted mapping and the current state into an ordered list of changes.

Pure functions. The order is chosen so that a crash part-way never leaves a button aimed at the wrong group:
unbind first, then clear stale slots, then build groups, then write slots, then bind.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from . import pico

SLOT_COUNT = 24
EP_BASE = 10
GROUP_PREFIX = "lutbridge_"
BIND_CLUSTERS = ("genOnOff", "genLevelCtrl")
# Buttons that get a Zigbee command. Favorite has no fallback action yet.
ACTIVE = pico.ON | pico.OFF | pico.RAISE | pico.LOWER


@dataclass(frozen=True)
class PicoTargets:
    """One Pico, with the Zigbee devices each button should reach (empty buttons omitted)."""
    serial: str
    name: str
    buttons: dict  # button bit -> frozenset[ieee]


@dataclass(frozen=True)
class Want:
    """One slot: a Pico, the buttons it covers, and the devices they reach."""
    serial: str
    mask: int
    members: frozenset
    label: str


@dataclass
class Current:
    groups: dict = field(default_factory=dict)  # group name -> frozenset[ieee]
    binds: dict = field(default_factory=dict)   # endpoint -> set[group name], for the bridge device
    slots: dict = field(default_factory=dict)   # slot index -> (serial hex, mask); only occupied slots
    slots_known: bool = True                    # False when the C6's slots could not be read back


@dataclass(frozen=True)
class Unbind:
    endpoint: int
    group: str


@dataclass(frozen=True)
class ClearSlot:
    index: int


@dataclass(frozen=True)
class RemoveGroup:
    name: str


@dataclass(frozen=True)
class CreateGroup:
    name: str


@dataclass(frozen=True)
class AddMember:
    group: str
    ieee: str


@dataclass(frozen=True)
class SetSlot:
    index: int
    serial: str
    mask: int


@dataclass(frozen=True)
class Bind:
    endpoint: int
    group: str


@dataclass
class Plan:
    actions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    slots: dict = field(default_factory=dict)  # final slot index -> Want
    groups: dict = field(default_factory=dict)  # final slot index -> group name its endpoint is bound to

    @property
    def empty(self) -> bool:
        return not self.actions


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "pico"


def wants_from(picos: Iterable[PicoTargets]) -> list[Want]:
    """Group a Pico's buttons that reach the same devices into one slot (one endpoint, one group)."""
    wants: list[Want] = []
    for p in sorted(picos, key=lambda x: x.serial):
        by_members: dict = {}
        for bit in (pico.ON, pico.OFF, pico.RAISE, pico.LOWER):
            members = p.buttons.get(bit)
            if members:
                by_members[members] = by_members.get(members, 0) | bit
        for members, mask in sorted(by_members.items(), key=lambda kv: kv[1]):
            wants.append(Want(p.serial, mask, members, p.name))
    return wants


def _allocate(wants: list[Want], current: Current, warnings: list[str]):
    """Stable slot assignment: a slot that already holds exactly this (serial, mask) keeps its index."""
    keep: dict[int, Want] = {}
    remaining: list[Want] = []
    held = dict(current.slots) if current.slots_known else {}  # unknown: rewrite everything
    for w in wants:
        idx = next((i for i, (s, m) in held.items() if s == w.serial and m == w.mask and i not in keep), None)
        if idx is None:
            remaining.append(w)
        else:
            keep[idx] = w
    assigned = dict(keep)
    free = (i for i in range(SLOT_COUNT) if i not in keep)
    for w in remaining:
        idx = next(free, None)
        if idx is None:
            warnings.append(f"no free slot for {w.label} ({w.serial}); only {SLOT_COUNT} endpoints exist")
            continue
        assigned[idx] = w
    return assigned, keep


def _group_names(wants: Iterable[Want], groups: dict) -> dict:
    """Group name per member set: reuse an existing group with exactly those members, else name a new one."""
    names: dict = {}
    by_members: dict = {}
    for name in sorted(groups):  # prefer owned groups, then alphabetical, when several match
        by_members.setdefault(groups[name], []).append(name)
    taken = set(groups)
    for w in wants:
        if w.members in names:
            continue
        existing = by_members.get(w.members)
        if existing:
            names[w.members] = sorted(existing, key=lambda n: (not n.startswith(GROUP_PREFIX), n))[0]
            continue
        base = GROUP_PREFIX + slug(w.label)
        name, n = base, 2
        while name in taken:
            name, n = f"{base}_{n}", n + 1
        taken.add(name)
        names[w.members] = name
    return names


def plan(wants: list[Want], current: Current) -> Plan:
    out = Plan()
    assigned, kept = _allocate(wants, current, out.warnings)
    out.slots = assigned
    group_of = _group_names([assigned[i] for i in sorted(assigned)], current.groups)
    desired_bind = {EP_BASE + i: group_of[w.members] for i, w in assigned.items()}
    out.groups = {i: group_of[w.members] for i, w in assigned.items()}

    # 1. unbind anything that is not wanted exactly as it is
    for ep, groups in sorted(current.binds.items()):
        for g in sorted(groups):
            if desired_bind.get(ep) != g:
                out.actions.append(Unbind(ep, g))

    # 2. clear slots that no longer match (stale), so a changed mask never overlaps an old one
    stale = sorted(current.slots) if current.slots_known else range(SLOT_COUNT)
    for idx in stale:
        if idx not in kept:
            out.actions.append(ClearSlot(idx))

    # 3. groups we created and nothing wants any more
    used = set(group_of.values())
    for name in sorted(current.groups):
        if name.startswith(GROUP_PREFIX) and name not in used:
            out.actions.append(RemoveGroup(name))

    # 4. new groups and their members
    for members, name in sorted(group_of.items(), key=lambda kv: kv[1]):
        if name not in current.groups:
            out.actions.append(CreateGroup(name))
            out.actions += [AddMember(name, ieee) for ieee in sorted(members)]

    # 5. slots that are new or changed
    for idx in sorted(assigned):
        if idx not in kept:
            w = assigned[idx]
            out.actions.append(SetSlot(idx, w.serial, w.mask))

    # 6. binds that are not already in place
    for ep in sorted(desired_bind):
        if desired_bind[ep] not in current.binds.get(ep, set()):
            out.actions.append(Bind(ep, desired_bind[ep]))
    return out
