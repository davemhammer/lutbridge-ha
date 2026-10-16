# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Turn the light entities an automation switches into the set of Zigbee2MQTT devices behind them.

Home Assistant light groups are expanded recursively. What an entity stands for in Zigbee2MQTT is supplied by the
caller (it comes from the entity and device registries), so this module stays free of Home Assistant imports.
"""
from __future__ import annotations

from typing import Callable, Iterable

MembersOf = Callable[[str], "list[str] | None"]  # light group entity -> member entity ids, or None if not a group
Z2mOf = Callable[[str], "frozenset[str] | None"]  # entity -> IEEE addresses it stands for, or None if not Zigbee2MQTT


def expand(entity_id: str, members_of: MembersOf, _seen: frozenset[str] = frozenset()) -> set[str]:
    """Leaf entities behind entity_id. A cycle between groups is cut instead of followed."""
    if entity_id in _seen:
        return set()
    members = members_of(entity_id)
    if not members:
        return {entity_id}
    out: set[str] = set()
    for m in members:
        out |= expand(m, members_of, _seen | {entity_id})
    return out


def resolve(entities: Iterable[str], members_of: MembersOf, z2m_of: Z2mOf) -> tuple[frozenset[str], set[str]]:
    """IEEE addresses reachable by Zigbee groupcast, and the leaf entities that are not Zigbee2MQTT devices."""
    devices: set[str] = set()
    unresolved: set[str] = set()
    for e in entities:
        for leaf in expand(e, members_of):
            ieees = z2m_of(leaf)
            if ieees:
                devices |= ieees
            else:
                unresolved.add(leaf)
    return frozenset(devices), unresolved
