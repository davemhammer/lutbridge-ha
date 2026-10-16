# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""From what Home Assistant knows to a plan: Picos and automations in, a Build (plan, issues, summary) out.

Pure and deterministic. The Home Assistant glue only supplies the inputs.
"""
from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field
from typing import Iterable

from . import derive, pico, planner, resolve

BUTTON_NAMES = {pico.ON: "on", pico.OFF: "off", pico.RAISE: "raise", pico.LOWER: "lower", pico.FAVORITE: "favorite"}


@dataclass(frozen=True)
class PicoInfo:
    device_id: str
    serial: str
    name: str
    area: str | None = None
    model: str | None = None


@dataclass
class Build:
    plan: planner.Plan
    targets: list = field(default_factory=list)          # PicoTargets, one per Pico that has a fallback
    issues: list = field(default_factory=list)           # things the owner should know, as text
    sources: set = field(default_factory=set)            # automations the active slots are derived from
    sources_by_serial: dict = field(default_factory=dict)  # Pico serial -> automations its slots are derived from
    plan_id: str = ""
    current: planner.Current = field(default_factory=planner.Current)  # what the plan was made against
    serial_names: dict = field(default_factory=dict)     # Pico serial -> name, for Picos that may no longer be wanted

    def review(self, light_names: dict[str, str] | None = None) -> str:
        """The plan for a person: one block per Pico saying what it will control, new / changed / unchanged,
        then what could not be covered, then the number of operations. Light names replace IEEE addresses."""
        names = light_names or {}
        cur, wants = self.current, self.plan.slots
        by_pico: dict = {}
        for i in sorted(wants):
            by_pico.setdefault(wants[i].serial, []).append(i)
        out: list[str] = []
        if self.plan.empty:
            out.append(f"Everything is up to date: {len(by_pico)} Pico(s) covered.")
        for serial in sorted(by_pico, key=lambda x: wants[by_pico[x][0]].label.lower()):
            idxs = by_pico[serial]
            label = wants[idxs[0]].label
            same = all(
                cur.slots.get(i) == (wants[i].serial, wants[i].mask)
                and self.plan.groups.get(i) in cur.binds.get(planner.EP_BASE + i, set())
                for i in idxs
            )
            had = any(s == serial for s, _m in cur.slots.values())
            out += ["", f"**{label}** ({'unchanged' if same else 'changed' if had else 'new'})"]
            for i in idxs:
                w = wants[i]
                buttons = ", ".join(BUTTON_NAMES[b] for b in (pico.ON, pico.OFF, pico.RAISE, pico.LOWER) if w.mask & b)
                lights = ", ".join(sorted(names.get(m, m) for m in w.members))
                group = self.plan.groups.get(i, "?")
                kind = "existing group" if group in cur.groups else "new group"
                out.append(f"- {buttons}: {lights}")
                out.append(f"- uses slot {i} (endpoint {planner.EP_BASE + i}) and {kind} `{group}`")
        gone = sorted({s for s, _m in cur.slots.values()} - set(by_pico))
        if gone:
            out += ["", "**No longer covered** (their fallback is removed)"]
            out += [f"- {self.serial_names.get(s, s)}" for s in gone]
        notes = [*self.plan.warnings, *self.issues]
        if notes:
            out += ["", "**Not covered / notes**"] + [f"- {t}" for t in notes]
        if not self.plan.empty:
            c = _counts(self.plan.actions)
            out += ["", "Operations: " + ", ".join(f"{n} {w}" for w, n in c.items() if n) + "."]
        return "\n".join(out).strip()

    def summary(self, names: dict[str, str] | None = None) -> str:
        names = names or {}
        lines = []
        for i in sorted(self.plan.slots):
            w = self.plan.slots[i]
            btn = "+".join(BUTTON_NAMES[b] for b in (pico.ON, pico.OFF, pico.RAISE, pico.LOWER) if w.mask & b)
            lines.append(f"- slot {i} (endpoint {planner.EP_BASE + i}): {w.label} [{btn}] -> {len(w.members)} light(s)")
        if self.plan.empty:
            lines.append("No changes are needed." if self.plan.slots else "Nothing to provision.")
        else:
            lines.append("")
            lines.append(f"{len(self.plan.actions)} change(s):")
            lines += [f"  - {_describe(a)}" for a in self.plan.actions]
        if self.issues or self.plan.warnings:
            lines += ["", "Notes:"] + [f"  - {t}" for t in [*self.plan.warnings, *self.issues]]
        return "\n".join(lines)


def _counts(actions) -> dict:
    P = planner
    kinds = {"group(s) created": P.CreateGroup, "light(s) added to groups": P.AddMember, "group(s) removed": P.RemoveGroup,
             "bind(s)": P.Bind, "unbind(s)": P.Unbind, "slot write(s)": P.SetSlot, "slot clear(s)": P.ClearSlot}
    return {w: sum(isinstance(a, t) for a in actions) for w, t in kinds.items()}


def effective_current(live: planner.Current, remembered: dict, force: bool = False) -> planner.Current:
    """The C6's slots as the planner should see them.

    A table read back from the C6 always wins. When it cannot be read back, believe what was last applied; with
    `force`, or when nothing is remembered, assume nothing so every slot is cleared and rewritten.
    """
    if force:
        return dataclasses.replace(live, slots={}, slots_known=False)
    if live.slots_known or not remembered:
        return live
    return dataclasses.replace(live, slots=dict(remembered), slots_known=True)


def _describe(a) -> str:
    P = planner
    if isinstance(a, P.SetSlot):
        return f"write slot {a.index}: {a.serial} mask 0x{a.mask:02x}"
    if isinstance(a, P.ClearSlot):
        return f"clear slot {a.index}"
    if isinstance(a, P.Bind):
        return f"bind endpoint {a.endpoint} to group {a.group}"
    if isinstance(a, P.Unbind):
        return f"unbind endpoint {a.endpoint} from group {a.group}"
    if isinstance(a, P.CreateGroup):
        return f"create group {a.name}"
    if isinstance(a, P.RemoveGroup):
        return f"remove group {a.name}"
    if isinstance(a, P.AddMember):
        return f"add {a.ieee} to group {a.group}"
    return repr(a)


def build(picos: dict[str, PicoInfo], automations: Iterable[dict], members_of, z2m_of, current: planner.Current) -> Build:
    intents, unmapped = derive.derive(automations, picos.keys())
    issues = [f"automation '{u.automation}' not used: {u.reason}" for u in unmapped]
    targets: list[planner.PicoTargets] = []
    sources: set[str] = set()
    by_serial: dict[str, set[str]] = {}

    for device_id in sorted(intents, key=lambda d: picos[d].name):
        info = picos[device_id]
        has = pico.buttons_for_model(info.model)
        buttons: dict = {}
        own: set[str] = set()
        for bit, t in sorted(intents[device_id].targets.items()):
            if not (bit & planner.ACTIVE & has):
                continue  # favorite has no action yet, and a button the model lacks cannot be pressed
            members, unresolved = resolve.resolve(sorted(t.entities), members_of, z2m_of)
            name = BUTTON_NAMES[bit]
            if not members:
                issues.append(f"{info.name} {name}: none of {sorted(t.entities)} is a Zigbee2MQTT light, so there is no fallback")
                continue
            if unresolved:
                issues.append(f"{info.name} {name}: not reachable by Zigbee and skipped: {sorted(unresolved)}")
            if "conditions" in t.flags:
                issues.append(f"{info.name} {name}: its automation has conditions, which the fallback ignores")
            if "complex" in t.flags:
                issues.append(f"{info.name} {name}: its automation does more than switch lights; only the lights are reproduced")
            buttons[bit] = members
            sources |= t.automations
            own |= t.automations
        if buttons:
            targets.append(planner.PicoTargets(info.serial, info.name, buttons))
            by_serial[info.serial] = own

    wants = planner.wants_from(targets)
    p = planner.plan(wants, current)
    plan_id = hashlib.sha1(repr((p.actions, sorted((i, w.serial, w.mask) for i, w in p.slots.items()))).encode()).hexdigest()[:10]
    serial_names = {info.serial: info.name for info in picos.values()}
    return Build(p, targets, issues, sources, by_serial, plan_id, current, serial_names)
