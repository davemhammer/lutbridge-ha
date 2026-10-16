# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Runs the integration: plans and applies the mapping, sends the heartbeat, installs the Zigbee2MQTT converter."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import collect
from .const import (
    BRIDGE_REPO_URL, CONF_BASE, DEFAULT_BASE, DOMAIN, DRIFT_CHECK_SECONDS, FIRST_DRIFT_CHECK_SECONDS, HEARTBEAT_SECONDS,
    NOTIFICATION_ID, SIGNAL_UPDATE, STORE_VERSION,
)
from .core import health, heartbeat, pipeline, z2m
from .core.manager import ApplyError, Orchestrator
from .hamqtt import HaMqtt

_LOGGER = logging.getLogger(__name__)


class Runtime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.base = entry.data.get(CONF_BASE, DEFAULT_BASE)
        self.orch = Orchestrator(HaMqtt(hass), self.base)
        self.store = Store(hass, STORE_VERSION, f"{DOMAIN}.state")
        self.sources: set[str] = set()  # automation aliases the applied mapping was derived from
        self.sources_by_serial: dict[str, set[str]] = {}  # the same, per Pico, so one broken automation affects one Pico
        self.names: dict[str, str] = {}  # Pico serial -> name, for messages
        self.on_fallback: dict[str, list[str]] = {}  # Pico name -> why it is being served by the fallback right now
        self.health = health.evaluate(health.Inputs(False, None, None, False, None, None, None))
        self.changes_pending: int | None = None  # operations a fresh plan would need, from the last drift check
        self._bundled_hash = ""  # fingerprint of the converter bundled with this version
        self._issues: dict[str, str] = {}  # Repairs issues currently raised, key -> the text shown
        self.applied_slots: dict[int, tuple[str, int]] = {}  # slot table as last applied (the C6 cannot be read back)
        self.converter_hash = ""  # fingerprint of the converter last installed in Zigbee2MQTT
        self._force_rewrite = False
        self.build: pipeline.Build | None = None  # the last plan shown for approval
        self.status = "no plan yet"
        self.heartbeat = "starting"
        self.last_claim: datetime | None = None
        self.picos_provisioned = 0
        self._counter = 0
        self._lock = asyncio.Lock()
        self._unsubs: list = []
        self._last_problem = ""

    # ---- lifecycle ----------------------------------------------------------------

    async def async_setup(self) -> None:
        saved = await self.store.async_load() or {}
        self.sources = set(saved.get("sources", []))
        self.picos_provisioned = int(saved.get("picos", 0))
        self.applied_slots = {int(i): (str(v[0]), int(v[1])) for i, v in (saved.get("slots") or {}).items()}
        self.sources_by_serial = {str(s): set(a) for s, a in (saved.get("sources_by_serial") or {}).items()}
        self.names = {str(s): str(n) for s, n in (saved.get("names") or {}).items()}
        self.converter_hash = str(saved.get("converter", ""))
        self._bundled_hash = z2m.converter_hash(await self._bundled_converter())
        await self.orch.start()
        self._unsubs.append(await self.orch.mqtt.subscribe(f"{self.base}/bridge/event", self._on_bridge_event))
        self._unsubs.append(async_track_time_interval(self.hass, self._beat, timedelta(seconds=HEARTBEAT_SECONDS)))
        self._unsubs.append(async_track_time_interval(self.hass, self._check_drift, timedelta(seconds=DRIFT_CHECK_SECONDS)))
        self._unsubs.append(async_call_later(self.hass, FIRST_DRIFT_CHECK_SECONDS, self._check_drift))
        self.hass.async_create_background_task(self._ensure_converter(), f"{DOMAIN} converter")

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self.orch.stop()
        for key in list(self._issues):
            ir.async_delete_issue(self.hass, DOMAIN, key)
        self._issues.clear()

    def _changed(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def _save(self) -> None:
        await self.store.async_save({
            "sources": sorted(self.sources), "picos": self.picos_provisioned,
            "slots": {str(i): [s, m] for i, (s, m) in sorted(self.applied_slots.items())},
            "sources_by_serial": {s: sorted(a) for s, a in sorted(self.sources_by_serial.items())},
            "names": dict(sorted(self.names.items())),
            "converter": self.converter_hash,
        })

    def light_names(self) -> dict[str, str]:
        """IEEE address -> friendly name, from Zigbee2MQTT's device list, for readable plans."""
        return {d["ieee_address"]: d.get("friendly_name", d["ieee_address"]) for d in self.orch.devices if d.get("ieee_address")}

    # ---- converter ----------------------------------------------------------------

    async def _ensure_converter(self) -> None:
        """Make sure Zigbee2MQTT runs the converter bundled with this version: install it when the pico bridge is
        unsupported, and again whenever the bundled file differs from the one installed last."""
        for _ in range(30):
            if self.orch.devices:
                break
            await asyncio.sleep(2)
        dev = next((d for d in self.orch.devices if d.get("model_id") == z2m.CONVERTER_MODEL
                    or (d.get("definition") or {}).get("model") == z2m.CONVERTER_MODEL), None)
        if dev is None:
            self.heartbeat = "pico bridge not paired with Zigbee2MQTT"
            self._changed()
            return
        external = (dev.get("definition") or {}).get("source") == "external"
        current = z2m.converter_hash(await self._bundled_converter())
        if external and self.converter_hash == current:
            return
        await self.async_install_converter()

    async def _bundled_converter(self) -> str:
        return await self.hass.async_add_executor_job(Path(__file__).with_name("pico_bridge.mjs").read_text)

    async def async_install_converter(self) -> None:
        code = await self._bundled_converter()
        await self.orch.install_converter(code)
        self.converter_hash = z2m.converter_hash(code)
        await self._save()
        _LOGGER.info("installed the pico_bridge Zigbee2MQTT converter (%s)", self.converter_hash)

    # ---- heartbeat ----------------------------------------------------------------

    def _on_bridge_event(self, topic, payload) -> None:
        """A device_announce from the C6 means it rebooted: disarm it now instead of at the next 15 s tick."""
        if isinstance(payload, dict) and payload.get("type") == "device_announce":
            if (payload.get("data") or {}).get("ieee_address") == self.orch.bridge:
                self.orch.claim_slots_unsupported = False  # it may have been reflashed; try per-Pico claims again
                self.hass.async_create_task(self._send_claim())

    def _claim_plan(self) -> heartbeat.ClaimPlan:
        """Which slots to claim now: every Pico whose source automations are all present and on."""
        sources = set(self.sources)
        for a in self.sources_by_serial.values():
            sources |= a
        problems = collect.automation_problem_map(self.hass, sources)
        return heartbeat.plan_claim(self.applied_slots, self.sources_by_serial, problems, self.names, self.sources)

    async def _beat(self, now=None) -> None:
        if not self.hass.is_running:
            return
        if not self.orch.bridge:
            self.heartbeat = "bridge not found in Zigbee2MQTT"
            self.on_fallback = {}
            self._publish_health()
            return
        await self._send_claim()

    async def _send_claim(self) -> None:
        """One heartbeat. Picos whose source automation is missing or off are left out of the claim, so the board
        serves just those (45 s after their last claim) while Home Assistant keeps the others."""
        plan = self._claim_plan()
        text = plan.describe()
        if text != self._last_problem:
            if text:
                _LOGGER.warning("leaving these Picos to the fallback: %s", text)
            else:
                _LOGGER.info("every Pico is back with Home Assistant")
        self._last_problem = text
        self.on_fallback = dict(plan.on_fallback)
        partial = bool(plan.on_fallback) and not plan.withheld_all
        if plan.withheld_all:
            self.heartbeat = f"withheld: {text}"  # nothing left for Home Assistant to claim
        elif partial and self.orch.claim_slots_unsupported:
            # The firmware cannot leave some Picos out of a claim, so keep to the safe rule: claim nothing, and the
            # board arms for every Pico.
            self.on_fallback = {name: reasons for name, reasons in plan.on_fallback.items()}
            self.heartbeat = f"withheld: {text} (firmware cannot claim per Pico)"
        else:
            self._counter = (self._counter + 1) & 0xFF
            await self.orch.claim(self._counter, plan.handled)
            self.last_claim = dt_util.utcnow()
            self.heartbeat = "sending" if not text else f"sending; fallback active for {text}"
        self._publish_health()

    # ---- health -------------------------------------------------------------------

    def _converter_current(self) -> bool | None:
        if not self._bundled_hash:
            return None
        installed = z2m.bridge_has_current_converter(self.orch.devices)
        return None if installed is None else installed and self.converter_hash == self._bundled_hash

    def _publish_health(self) -> None:
        """Re-evaluate health, raise or clear Repairs issues, and tell the entities."""
        age = (dt_util.utcnow() - self.last_claim).total_seconds() if self.last_claim else None
        self.health = health.evaluate(health.Inputs(
            bridge_known=bool(self.orch.bridge), z2m_online=self.orch.z2m_online, board_online=self.orch.board_online,
            heartbeat_ok=self.heartbeat.startswith("sending"), claim_age_s=age,
            converter_current=self._converter_current(), changes_pending=self.changes_pending, on_fallback=self.on_fallback,
            firmware_supports_slots=not self.orch.claim_slots_unsupported))
        for key, detail in self.health.problems.items():
            if self._issues.get(key) != detail:  # raise it when new or reworded, not on every heartbeat
                self._issues[key] = detail
                ir.async_create_issue(self.hass, DOMAIN, key, is_fixable=False, severity=_severity(key),
                                      translation_key=key, translation_placeholders={"detail": detail, "bridge_url": BRIDGE_REPO_URL})
        for key in sorted(set(self._issues) - set(self.health.problems)):
            ir.async_delete_issue(self.hass, DOMAIN, key)
            del self._issues[key]
        self._changed()

    async def _check_drift(self, now=None) -> None:
        """Quietly re-plan to see whether the mapping still matches Home Assistant (a new Pico, a renamed group, a
        removed light). It changes nothing; it only raises the 'out of date' problem."""
        if not self.hass.is_running or not self.orch.bridge or not self.orch.devices:
            return
        try:
            async with self._lock:
                build = await self._compute(refresh=False)
        except Exception:  # noqa: BLE001 - a failed check must never break the heartbeat
            _LOGGER.exception("could not check whether the mapping is up to date")
            return
        self.changes_pending = len(build.plan.actions)
        if not build.plan.actions:
            await self._adopt_sources(build)
        self._publish_health()

    async def _adopt_sources(self, build: pipeline.Build) -> None:
        """The mapping already matches Home Assistant, so the automations in this plan are the ones the applied mapping
        comes from. Take them over when they differ from what was remembered: this upgrades state saved before sources
        were tracked per Pico (without it, one broken automation would withhold the whole claim), and follows an
        automation that was renamed."""
        sources = {s: set(a) for s, a in build.sources_by_serial.items()}
        names = {w.serial: w.label for w in build.plan.slots.values()}
        if sources == self.sources_by_serial and names == self.names and set(build.sources) == self.sources:
            return
        self.sources_by_serial, self.names, self.sources = sources, names, set(build.sources)
        await self._save()
        _LOGGER.info("remembered which automations feed each of %d Pico(s)", len(sources))

    # ---- plan and apply -----------------------------------------------------------

    async def _compute(self, refresh: bool = True) -> pipeline.Build:
        automations = await collect.load_automations(self.hass)
        picos = collect.pico_devices(self.hass)
        by_name, _ = z2m.parse_groups(self.orch.groups)
        members_of, z2m_of = collect.resolvers(self.hass, by_name)
        if refresh:
            await self.orch.refresh_slots()
        current = pipeline.effective_current(self.orch.current(), self.applied_slots, self._force_rewrite)
        return pipeline.build(picos, automations, members_of, z2m_of, current)

    def _notify(self, title: str, build: pipeline.Build, intro: str, extra: str = "") -> None:
        persistent_notification.async_create(
            self.hass, f"{intro}\n\n{build.review(self.light_names())}{extra}\n\nPlan id `{build.plan_id}`.",
            title=title, notification_id=NOTIFICATION_ID)

    async def async_plan(self, rewrite_slots: bool = False) -> pipeline.Build:
        """Work out the plan. `rewrite_slots` ignores what was remembered and clears and rewrites every slot."""
        async with self._lock:
            self._force_rewrite = rewrite_slots
            build = await self._compute()
            self.build = build
            n = len(build.plan.actions)
            self.status = "up to date" if n == 0 else f"{n} changes pending"
            self.changes_pending = n
            self._notify("Lutron Pico fallback: plan", build,
                         "Review these changes, then press **Apply plan** (or open Configure on the integration).")
            self._publish_health()
            return build

    async def async_apply(self, plan_id: str | None = None) -> None:
        async with self._lock:
            shown = self.build
            if shown is None:
                raise HomeAssistantError("make a plan first")
            if plan_id and plan_id != shown.plan_id:
                raise HomeAssistantError(f"plan {plan_id} is not the plan that was shown ({shown.plan_id})")
            fresh = await self._compute()
            if fresh.plan_id != shown.plan_id:
                self.status = "the system changed since the plan was made; plan again"
                self._changed()
                raise HomeAssistantError(self.status)
            try:
                await self.orch.apply(fresh.plan)
            except ApplyError as err:
                self.status = f"apply failed: {err}"
                self._changed()
                raise HomeAssistantError(self.status) from err
            wanted = {i: (w.serial, w.mask) for i, w in fresh.plan.slots.items()}
            diff = await self.orch.verify_slots(wanted) if self.orch.current().slots_known else []
            self.applied_slots = wanted
            self.sources = set(fresh.sources)
            self.sources_by_serial = {s: set(a) for s, a in fresh.sources_by_serial.items()}
            self.names = {w.serial: w.label for w in fresh.plan.slots.values()}
            self.picos_provisioned = len({w.serial for w in fresh.plan.slots.values()})
            self._force_rewrite = False
            await self._save()
            self.status = "applied" if not diff else f"applied, but the C6 slots differ: {'; '.join(diff)}"
            self._notify("Lutron Pico fallback: applied", fresh, "Applied.", f"\n\nSlot check: {diff}" if diff else "")
            self.build = await self._compute()  # what is left to do now, normally nothing
            self.changes_pending = len(self.build.plan.actions)
            self._publish_health()


def _severity(key: str) -> ir.IssueSeverity:
    """Problems that leave Picos unprotected are errors; the rest need attention but protection still works."""
    return ir.IssueSeverity.ERROR if key in ("board_not_found", "board_offline", "heartbeat") else ir.IssueSeverity.WARNING
