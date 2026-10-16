# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Smoke tests for the Home Assistant glue, run against stand-ins for the homeassistant modules.

They cannot prove the glue works inside Home Assistant (that needs a live instance), but they catch import errors,
misspelled names and mistakes in the heartbeat logic, which would otherwise surface on the owner's system.
"""
import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "lutbridge"


class StubMeta(type):
    def __getattr__(cls, name):  # enum-like access such as SensorDeviceClass.TIMESTAMP
        if name.startswith("__"):
            raise AttributeError(name)
        return Stub()


class Stub(metaclass=StubMeta):
    """Accepts anything: subclassing, calling, subscripting, attribute access."""

    def __init_subclass__(cls, **kw):
        pass

    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return Stub()

    def __class_getitem__(cls, item):
        return cls

    def __getattr__(self, name):
        return Stub()


class StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        sub = sys.modules.get(f"{self.__name__}.{name}")  # `from pkg import submodule` must find the submodule
        return sub if sub is not None else Stub


@pytest.fixture(scope="module", autouse=True)
def fake_homeassistant():
    names = [
        "homeassistant", "homeassistant.components", "homeassistant.components.mqtt",
        "homeassistant.components.persistent_notification", "homeassistant.components.sensor",
        "homeassistant.components.button", "homeassistant.components.binary_sensor", "homeassistant.config_entries",
        "homeassistant.core", "homeassistant.exceptions", "homeassistant.helpers", "homeassistant.helpers.area_registry",
        "homeassistant.helpers.device_registry", "homeassistant.helpers.entity_registry", "homeassistant.helpers.dispatcher",
        "homeassistant.helpers.event", "homeassistant.helpers.issue_registry", "homeassistant.helpers.storage", "homeassistant.helpers.entity", "homeassistant.util",
        "homeassistant.util.dt", "homeassistant.util.yaml",
    ]
    saved = {n: sys.modules.get(n) for n in names + ["voluptuous"]}
    for n in names + ["voluptuous"]:
        sys.modules[n] = StubModule(n)
    sys.modules["homeassistant.exceptions"].HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    def _callback(fn):  # like the real @callback: marks the function and returns it
        fn._hass_callback = True
        return fn

    sys.modules["homeassistant.core"].callback = _callback
    sys.modules["homeassistant.util.dt"].utcnow = lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    sys.path.insert(0, str(ROOT / "custom_components"))
    yield
    sys.path.remove(str(ROOT / "custom_components"))
    for n, m in saved.items():
        if m is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = m
    for n in [k for k in sys.modules if k.startswith("lutbridge")]:
        del sys.modules[n]


def test_every_glue_module_imports():
    for mod in ("const", "hamqtt", "collect", "runtime", "entity", "sensor", "button", "binary_sensor", "config_flow", "__init__"):
        importlib.import_module("lutbridge" if mod == "__init__" else f"lutbridge.{mod}")


def test_the_bundled_converter_carries_the_commands_the_integration_sends():
    # The converter is owned by lutbridge-esp32; CI compares this copy with that repository's.
    code = (PKG / "pico_bridge.mjs").read_text()
    for needle in ("0xef00", "'claim'", "claimSlots", "setSlot", "ensureCluster"):
        assert needle in code or needle.lower() in code.lower(), needle


def test_manifest_and_strings_are_valid():
    manifest = json.loads((PKG / "manifest.json").read_text())
    assert manifest["domain"] == "lutbridge" and "mqtt" in manifest["dependencies"] and manifest["config_flow"] is True
    assert json.loads((PKG / "strings.json").read_text()) == json.loads((PKG / "translations" / "en.json").read_text())


class FakeState:
    def __init__(self, name, state):
        self.state = state
        self.attributes = {"friendly_name": name}


class FakeStates:
    def __init__(self, autos):
        self.autos = autos

    def async_all(self, domain):
        return [FakeState(n, s) for n, s in self.autos.items()]


class FakeHass:
    def __init__(self, autos, running=True):
        self.states = FakeStates(autos)
        self.is_running = running
        self.tasks = []

    def async_create_task(self, coro):
        self.tasks.append(coro)


def test_automation_problems_names_missing_and_disabled_automations():
    collect = importlib.import_module("lutbridge.collect")
    hass = FakeHass({"A": "on", "B": "off"})
    assert collect.automation_problems(hass, {"A", "B", "C"}) == ["automation 'B' is off", "automation 'C' no longer exists"]
    assert collect.automation_problems(hass, {"A"}) == []


class FakeOrch:
    def __init__(self, bridge="0xabc"):
        self.bridge = bridge
        self.claims = []
        self.handled = []
        self.devices = []
        self.z2m_online = True
        self.claim_slots_unsupported = False
        self.board_online = True

    async def claim(self, n, handled=None):
        self.claims.append(n)
        self.handled.append(handled)


def make_runtime(autos, sources, bridge="0xabc", running=True):
    rt_mod = importlib.import_module("lutbridge.runtime")
    rt = rt_mod.Runtime.__new__(rt_mod.Runtime)  # skip the Home Assistant wiring in __init__
    rt.hass = FakeHass(autos, running)
    rt.orch = FakeOrch(bridge)
    rt.sources = set(sources)
    rt.sources_by_serial, rt.names, rt.on_fallback, rt.applied_slots = {}, {}, {}, {}
    rt.heartbeat, rt.last_claim, rt._counter, rt._last_problem = "starting", None, 0, ""
    rt.health_mod = importlib.import_module("lutbridge.core.health")
    rt.health = rt.health_mod.evaluate(rt.health_mod.Inputs(False, None, None, False, None, None, None))
    rt.changes_pending, rt._bundled_hash, rt._issues, rt.converter_hash = None, "", {}, ""
    rt._changed = lambda: None
    return rt


def test_heartbeat_is_sent_while_the_source_automations_are_on():
    rt = make_runtime({"Pico on": "on"}, {"Pico on"})
    asyncio.run(rt._beat())
    assert rt.orch.claims == [1] and rt.heartbeat == "sending"


def test_heartbeat_is_withheld_when_a_source_automation_is_off_so_the_fallback_arms():
    rt = make_runtime({"Pico on": "off"}, {"Pico on"})
    asyncio.run(rt._beat())
    assert rt.orch.claims == [] and rt.heartbeat.startswith("withheld:")


def test_heartbeat_waits_for_home_assistant_to_finish_starting():
    rt = make_runtime({}, set(), running=False)
    asyncio.run(rt._beat())
    assert rt.orch.claims == []


def test_heartbeat_reports_a_missing_bridge_instead_of_sending():
    rt = make_runtime({}, set(), bridge=None)
    asyncio.run(rt._beat())
    assert rt.orch.claims == [] and "not found" in rt.heartbeat


def test_claim_counter_changes_every_beat_and_wraps_a_byte():
    rt = make_runtime({}, set())
    rt._counter = 255
    asyncio.run(rt._beat())
    asyncio.run(rt._beat())
    assert rt.orch.claims == [0, 1]


# ---- registry readers (collect.py) against fake registries ------------------------------------------------------

class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeDevReg:
    def __init__(self, devices):
        self.devices = {d.id: d for d in devices}

    def async_get(self, device_id):
        return self.devices.get(device_id)


class FakeEntReg:
    def __init__(self, entries):
        self.entries = entries

    def async_get(self, entity_id):
        return self.entries.get(entity_id)


class FakeAreaReg:
    def async_get_area(self, area_id):
        return Obj(name="Dining Room") if area_id == "a1" else None


def _dev(i, ids, model="x", name="n", area=None):
    return Obj(id=i, identifiers=ids, model=model, name=name, name_by_user=None, area_id=area)


def test_pico_devices_survive_identifiers_that_are_not_pairs(monkeypatch):
    collect = importlib.import_module("lutbridge.collect")
    devs = FakeDevReg([
        _dev("pico", {("lutron_caseta", 93694086)}, "PJ2-3BRL-GXX-X01 (Pico3ButtonRaiseLower)", "Dining Room Pico", "a1"),
        _dev("odd", {("matter", "node", "extra"), ("zha", "ieee", "ep", "cluster")}),  # three and four elements: used to crash
        _dev("bridge", {("lutron_caseta", "123456")}, "L-BDGPRO2-WH (SmartBridge)"),
        _dev("badid", {("lutron_caseta", "not-a-number")}, "PJ2-2B-GXX-X01 (Pico2Button)"),
    ])
    monkeypatch.setattr(collect.dr, "async_get", lambda hass: devs, raising=False)
    monkeypatch.setattr(collect.ar, "async_get", lambda hass: FakeAreaReg(), raising=False)
    out = collect.pico_devices(None)
    assert list(out) == ["pico"]
    assert out["pico"].serial == "0595a886" and out["pico"].name == "Dining Room Pico" and out["pico"].area == "Dining Room"


def test_resolvers_read_zigbee2mqtt_devices_groups_and_light_groups(monkeypatch):
    collect = importlib.import_module("lutbridge.collect")
    devs = FakeDevReg([
        _dev("d1", {("mqtt", "zigbee2mqtt_0xabc"), ("mqtt", "weird", "three")}, "Bulb", "Kitchen Fan 1"),
        _dev("g1", {("mqtt", "zigbee2mqtt_1221051039_8")}, "Group", "Master Bedroom Zigbee Group"),
        _dev("c1", {("lutron_caseta", 5)}, "Dimmer", "Bar"),
    ])
    ents = FakeEntReg({
        "light.fan": Obj(platform="mqtt", device_id="d1"),
        "light.bedroom": Obj(platform="mqtt", device_id="g1"),
        "light.bar": Obj(platform="lutron_caseta", device_id="c1"),
        "light.kitchen_group": Obj(platform="group", device_id=None),
    })
    monkeypatch.setattr(collect.dr, "async_get", lambda hass: devs, raising=False)
    monkeypatch.setattr(collect.er, "async_get", lambda hass: ents, raising=False)
    hass = Obj(states=Obj(get=lambda e: Obj(attributes={"entity_id": ["light.fan", "light.bar"]}) if e == "light.kitchen_group" else None))
    members_of, z2m_of = collect.resolvers(hass, {"Master Bedroom Zigbee Group": frozenset({"0x1", "0x2"})})
    assert z2m_of("light.fan") == frozenset({"0xabc"})
    assert z2m_of("light.bedroom") == frozenset({"0x1", "0x2"})
    assert z2m_of("light.bar") is None and z2m_of("light.missing") is None
    assert members_of("light.kitchen_group") == ["light.fan", "light.bar"] and members_of("light.fan") is None


def test_ha_mqtt_adapter_subscribes_with_a_loop_safe_callback_that_decodes_json(monkeypatch):
    hamqtt = importlib.import_module("lutbridge.hamqtt")
    captured = {}

    async def fake_subscribe(hass, topic, cb):
        captured["topic"], captured["cb"] = topic, cb
        return lambda: captured.setdefault("unsubscribed", True)

    monkeypatch.setattr(hamqtt.mqtt, "async_subscribe", fake_subscribe, raising=False)
    got = []
    unsub = asyncio.run(hamqtt.HaMqtt(None).subscribe("zigbee2mqtt/bridge/groups", lambda topic, payload: got.append((topic, payload))))
    cb = captured["cb"]
    assert captured["topic"] == "zigbee2mqtt/bridge/groups"
    assert cb._hass_callback is True  # runs on the event loop, not in a worker thread
    cb(Obj(topic="t/json", payload='{"a": 1}'))
    cb(Obj(topic="t/text", payload="online"))
    cb(Obj(topic="t/empty", payload=""))
    assert got == [("t/json", {"a": 1}), ("t/text", "online"), ("t/empty", None)]
    unsub()
    assert captured["unsubscribed"]


def test_ha_mqtt_adapter_publishes_json_for_dicts_and_passes_strings_through(monkeypatch):
    hamqtt = importlib.import_module("lutbridge.hamqtt")
    sent = []

    async def fake_publish(hass, topic, payload):
        sent.append((topic, payload))

    monkeypatch.setattr(hamqtt.mqtt, "async_publish", fake_publish, raising=False)
    mq = hamqtt.HaMqtt(None)
    asyncio.run(mq.publish("a/set", {"claim": 3}))
    asyncio.run(mq.publish("a/raw", "text"))
    assert sent == [("a/set", '{"claim": 3}'), ("a/raw", "text")]


def test_the_orchestrator_works_through_the_real_adapter(monkeypatch):
    """HaMqtt subscribe -> orchestrator handlers: the wiring that failed in a live Home Assistant."""
    hamqtt = importlib.import_module("lutbridge.hamqtt")
    manager = importlib.import_module("lutbridge.core.manager")
    subs = {}

    async def fake_subscribe(hass, topic, cb):
        subs[topic] = cb
        return lambda: None

    monkeypatch.setattr(hamqtt.mqtt, "async_subscribe", fake_subscribe, raising=False)

    async def go():
        orch = manager.Orchestrator(hamqtt.HaMqtt(None), "zigbee2mqtt")
        await orch.start()  # raised TypeError at @callback when the parameter shadowed the decorator
        subs["zigbee2mqtt/bridge/groups"](Obj(topic="zigbee2mqtt/bridge/groups", payload='[{"id": 1, "friendly_name": "g", "members": []}]'))
        await asyncio.sleep(0)
        assert orch.groups and orch.groups[0]["friendly_name"] == "g"
    asyncio.run(go())


def test_all_devices_works_whether_iterating_the_registry_yields_entries_or_keys():
    collect = importlib.import_module("lutbridge.collect")
    a, b = _dev("a", {("x", "1")}), _dev("b", {("x", "2")})

    class NewStyle:  # iterating yields entries and there is no .values(): calling it would be a deprecated access
        devices = [a, b]

    class OldStyle:
        devices = {"a": a, "b": b}

    class Empty:
        devices = {}

    assert collect._all_devices(NewStyle()) == [a, b]
    assert collect._all_devices(OldStyle()) == [a, b]
    assert collect._all_devices(Empty()) == []


# ---- converter versioning and the plan / apply cycle against a fake world ----------------------------------------

class ConverterOrch:
    def __init__(self, devices):
        self.devices = devices
        self.installed = []

    async def install_converter(self, code):
        self.installed.append(code)


class FakeStore:
    def __init__(self):
        self.saved = None

    async def async_save(self, data):
        self.saved = data


def converter_runtime(devices, known_hash):
    rt = make_runtime({}, set())
    rt.orch = ConverterOrch(devices)
    rt.store = FakeStore()
    rt.converter_hash = known_hash
    rt.applied_slots, rt.sources, rt.picos_provisioned = {}, set(), 0

    async def bundled():
        return "export default {}"

    rt._bundled_converter = bundled
    return rt


def bridge_device(source):
    return {"ieee_address": "0xabc", "model_id": "l-re.pico-bridge", "definition": {"source": source, "model": "l-re.pico-bridge"}}


def test_converter_is_installed_when_the_bridge_is_unsupported():
    z2m = importlib.import_module("lutbridge.core.z2m")
    rt = converter_runtime([bridge_device("generated")], "")
    asyncio.run(rt._ensure_converter())
    assert rt.orch.installed == ["export default {}"] and rt.converter_hash == z2m.converter_hash("export default {}")
    assert rt.store.saved["converter"] == rt.converter_hash


def test_converter_is_left_alone_when_the_installed_one_matches_the_bundled_one():
    z2m = importlib.import_module("lutbridge.core.z2m")
    rt = converter_runtime([bridge_device("external")], z2m.converter_hash("export default {}"))
    asyncio.run(rt._ensure_converter())
    assert rt.orch.installed == []


def test_converter_is_reinstalled_when_the_bundled_one_changed():
    rt = converter_runtime([bridge_device("external")], "an-older-version")
    asyncio.run(rt._ensure_converter())
    assert len(rt.orch.installed) == 1


def test_missing_bridge_is_reported_and_nothing_installed():
    rt = converter_runtime([{"ieee_address": "0x1"}], "")
    asyncio.run(rt._ensure_converter())
    assert rt.orch.installed == [] and "not paired" in rt.heartbeat


class WorldOrch:
    """Zigbee2MQTT as the runtime sees it: groups and binds are readable, the C6's slots are not."""

    def __init__(self):
        self.devices, self.groups = [], []
        self.cur = None
        self.plans = []
        self.bridge, self.z2m_online, self.board_online = "0xabc", True, True
        self.claim_slots_unsupported = False

    async def refresh_slots(self):
        return False

    def current(self):
        return self.cur

    async def apply(self, plan):
        # Simulate with the same planner module the runtime uses (lutbridge.core.planner), not the test-path copy.
        P = importlib.import_module("lutbridge.core.planner")
        self.plans.append(plan)
        groups = {k: set(v) for k, v in self.cur.groups.items()}
        binds = {k: set(v) for k, v in self.cur.binds.items()}
        for a in plan.actions:
            if isinstance(a, P.Unbind):
                binds.get(a.endpoint, set()).discard(a.group)
            elif isinstance(a, P.RemoveGroup):
                groups.pop(a.name, None)
            elif isinstance(a, P.CreateGroup):
                groups[a.name] = set()
            elif isinstance(a, P.AddMember):
                groups[a.group].add(a.ieee)
            elif isinstance(a, P.Bind):
                binds.setdefault(a.endpoint, set()).add(a.group)
        # the C6's slots are not readable, so the world reports them as unknown
        self.cur = P.Current({k: frozenset(v) for k, v in groups.items()}, {k: v for k, v in binds.items() if v}, {}, slots_known=False)

    async def verify_slots(self, want):
        return []


def flow_runtime(monkeypatch):
    from core import planner
    from test_derive import LIVING_ROOM_BLUEPRINT
    from test_pipeline import GROUPS, PICOS, Z2M
    collect = importlib.import_module("lutbridge.collect")

    async def autos(hass):
        return [LIVING_ROOM_BLUEPRINT]

    monkeypatch.setattr(collect, "load_automations", autos)
    monkeypatch.setattr(collect, "pico_devices", lambda hass: PICOS)
    monkeypatch.setattr(collect, "resolvers", lambda hass, groups: (GROUPS.get, Z2M.get))
    rt = make_runtime({}, set())
    rt.orch = WorldOrch()
    rt.orch.cur = planner.Current(slots_known=False)
    rt.store = FakeStore()
    rt._lock = asyncio.Lock()
    rt.build, rt.status, rt.applied_slots, rt.sources, rt.picos_provisioned = None, "", {}, set(), 0
    rt.converter_hash, rt._force_rewrite = "", False
    return rt


def test_after_an_apply_the_plan_reads_up_to_date_even_though_slots_cannot_be_read_back(monkeypatch):
    async def go():
        rt = flow_runtime(monkeypatch)
        build = await rt.async_plan()
        assert not build.plan.empty and "changes pending" in rt.status
        await rt.async_apply(build.plan_id)
        assert rt.status == "applied" and rt.applied_slots and rt.store.saved["slots"]
        again = await rt.async_plan()
        assert again.plan.empty and rt.status == "up to date"
        forced = await rt.async_plan(rewrite_slots=True)  # reflashed C6: rewrite everything
        assert not forced.plan.empty and any(type(a).__name__ == "ClearSlot" for a in forced.plan.actions)
    asyncio.run(go())


class FakeBuild:
    plan_id = "abc123"

    def review(self, names):
        return "**Living Room Pico** (new)"


class OptionsRuntime:
    def __init__(self, fail=False):
        self.build = None
        self.fail = fail
        self.planned = self.applied = 0

    def light_names(self):
        return {}

    async def async_plan(self):
        self.planned += 1
        self.build = FakeBuild()
        return self.build

    async def async_apply(self):
        self.applied += 1
        if self.fail:
            raise sys.modules["homeassistant.exceptions"].HomeAssistantError("Bind failed: no response")


def options_flow(runtime):
    cf = importlib.import_module("lutbridge.config_flow")
    flow = cf.LutBridgeOptionsFlow()
    flow.config_entry = Obj(runtime_data=runtime)
    flow.async_show_form = lambda **kw: ("form", kw)
    flow.async_create_entry = lambda **kw: ("entry", kw)
    return flow


def test_configure_shows_the_review_and_applies_nothing_until_asked():
    rt = OptionsRuntime()
    kind, form = asyncio.run(options_flow(rt).async_step_init())
    assert kind == "form" and "**Living Room Pico** (new)" in form["description_placeholders"]["review"]
    assert form["description_placeholders"]["plan_id"] == "abc123" and rt.applied == 0
    kind, _ = asyncio.run(options_flow(rt).async_step_init({"apply": False}))
    assert kind == "entry" and rt.applied == 0


def test_configure_applies_when_the_owner_turns_the_switch_on():
    rt = OptionsRuntime()
    asyncio.run(options_flow(rt).async_step_init())
    kind, _ = asyncio.run(options_flow(rt).async_step_init({"apply": True}))
    assert kind == "entry" and rt.applied == 1


def test_configure_shows_the_failure_and_the_same_plan_when_the_apply_fails():
    rt = OptionsRuntime(fail=True)
    asyncio.run(options_flow(rt).async_step_init())
    kind, form = asyncio.run(options_flow(rt).async_step_init({"apply": True}))
    assert kind == "form" and form["errors"] == {"base": "apply_failed"}
    assert "Bind failed" in form["description_placeholders"]["problem"] and rt.planned == 1  # no needless re-plan


class IssueLog:
    """Stands in for Home Assistant's issue registry: records which Repairs issues are open."""

    class IssueSeverity:
        ERROR = "error"
        WARNING = "warning"

    def __init__(self):
        self.open = {}
        self.created = 0

    def async_create_issue(self, hass, domain, key, **kw):
        self.created += 1
        self.open[key] = kw

    def async_delete_issue(self, hass, domain, key):
        self.open.pop(key, None)


def per_pico_runtime(monkeypatch, autos):
    rt = make_runtime(autos, set())
    rt.applied_slots = {0: ("aaaa0001", 0x0F), 1: ("aaaa0002", 0x03)}
    rt.sources_by_serial = {"aaaa0001": {"Kitchen"}, "aaaa0002": {"Hall"}}
    rt.names = {"aaaa0001": "Kitchen Pico", "aaaa0002": "Hall Pico"}
    log = IssueLog()
    monkeypatch.setattr(importlib.import_module("lutbridge.runtime"), "ir", log)
    return rt, log


def test_only_the_pico_with_a_broken_automation_is_left_to_the_fallback(monkeypatch):
    rt, log = per_pico_runtime(monkeypatch, {"Kitchen": "on", "Hall": "off"})
    asyncio.run(rt._beat())
    handled = rt.orch.handled[-1]
    assert 0 in handled and 1 not in handled and 2 in handled  # Kitchen claimed, Hall left out, unused slots claimed
    assert rt.heartbeat.startswith("sending; fallback active for Hall Pico")
    assert list(rt.on_fallback) == ["Hall Pico"]
    assert "on_fallback" in log.open and "Hall Pico" in log.open["on_fallback"]["translation_placeholders"]["detail"]
    assert not rt.health.ok


def test_the_pico_returns_to_home_assistant_when_its_automation_is_back_on(monkeypatch):
    rt, log = per_pico_runtime(monkeypatch, {"Kitchen": "on", "Hall": "off"})
    asyncio.run(rt._beat())
    rt.hass.states.autos["Hall"] = "on"
    asyncio.run(rt._beat())
    assert 1 in rt.orch.handled[-1] and rt.heartbeat == "sending" and rt.on_fallback == {}
    assert log.open == {} and rt.health.ok  # the Repairs issue clears itself


def test_nothing_is_sent_when_every_pico_is_left_to_the_fallback(monkeypatch):
    rt, log = per_pico_runtime(monkeypatch, {"Kitchen": "off", "Hall": "unavailable"})
    asyncio.run(rt._beat())
    assert rt.orch.claims == [] and rt.heartbeat.startswith("withheld:") and len(rt.on_fallback) == 2


def test_a_missing_automation_counts_like_a_switched_off_one(monkeypatch):
    rt, _ = per_pico_runtime(monkeypatch, {"Kitchen": "on"})  # 'Hall' no longer exists
    asyncio.run(rt._beat())
    assert 1 not in rt.orch.handled[-1] and "no longer exists" in rt.on_fallback["Hall Pico"][0]


def test_an_offline_board_raises_an_error_issue_and_clears_it_when_it_returns(monkeypatch):
    rt, log = per_pico_runtime(monkeypatch, {"Kitchen": "on", "Hall": "on"})
    rt.orch.board_online = False
    asyncio.run(rt._beat())
    assert log.open["board_offline"]["severity"] == "error" and log.open["board_offline"]["is_fixable"] is False
    rt.orch.board_online = True
    asyncio.run(rt._beat())
    assert log.open == {}


def test_an_unchanged_problem_is_raised_once_not_on_every_heartbeat(monkeypatch):
    rt, log = per_pico_runtime(monkeypatch, {"Kitchen": "on", "Hall": "off"})
    for _ in range(4):
        asyncio.run(rt._beat())
    assert log.created == 1
    rt.hass.states.autos["Kitchen"] = "off"  # now every Pico is left out: the text changes and the heartbeat stops
    asyncio.run(rt._beat())
    assert log.created == 3 and set(log.open) == {"on_fallback", "heartbeat"}


def test_old_firmware_that_cannot_claim_per_pico_falls_back_to_all_or_nothing(monkeypatch):
    rt, log = per_pico_runtime(monkeypatch, {"Kitchen": "on", "Hall": "off"})
    rt.orch.claim_slots_unsupported = True  # Zigbee2MQTT reported UNSUP_COMMAND for the per-slot claim
    asyncio.run(rt._beat())
    assert rt.orch.claims == [] and rt.heartbeat.startswith("withheld:") and "firmware cannot claim per Pico" in rt.heartbeat
    assert "firmware" in log.open and "lutbridge-esp32" in log.open["firmware"]["translation_placeholders"]["detail"]
    # with nothing left out the plain claim is fine on any firmware
    rt.hass.states.autos["Hall"] = "on"
    asyncio.run(rt._beat())
    assert rt.orch.claims == [1] and rt.heartbeat == "sending"
    assert "on_fallback" not in log.open


def test_a_reboot_announcement_makes_the_integration_try_per_pico_claims_again(monkeypatch):
    rt, _ = per_pico_runtime(monkeypatch, {"Kitchen": "on", "Hall": "on"})
    rt.orch.claim_slots_unsupported = True
    rt.base = "zigbee2mqtt"
    rt.orch.bridge = "0xabc"
    rt._on_bridge_event("t", {"type": "device_announce", "data": {"ieee_address": "0xabc"}})
    assert rt.orch.claim_slots_unsupported is False  # the board may have been reflashed
    rt._on_bridge_event("t", {"type": "device_announce", "data": {"ieee_address": "0xother"}})
    rt._on_bridge_event("t", {"type": "something_else"})
    assert len(rt.hass.tasks) == 1  # only the announcement from our own board schedules a claim
    for coro in rt.hass.tasks:
        coro.close()  # the fake Home Assistant keeps the coroutine instead of running it


def test_unknown_board_availability_raises_nothing(monkeypatch):
    rt, log = per_pico_runtime(monkeypatch, {"Kitchen": "on", "Hall": "on"})
    rt.orch.board_online = None
    asyncio.run(rt._beat())
    assert log.open == {} and rt.health.ok and rt.health.board == "unknown"


def test_the_drift_check_notices_a_stale_mapping_and_changes_nothing(monkeypatch):
    rt, log = per_pico_runtime(monkeypatch, {"Kitchen": "on", "Hall": "on"})
    rt._lock = asyncio.Lock()
    rt.orch.devices = [{"ieee_address": "0xabc"}]
    seen = {}

    class B:
        plan = Obj(actions=[1, 2, 3], slots={})
        sources_by_serial = {s: set(a) for s, a in rt.sources_by_serial.items()}
        sources = set(rt.sources)

    async def compute(refresh=True):
        seen["refresh"] = refresh
        return B()

    rt._compute = compute
    asyncio.run(rt._check_drift())
    assert seen["refresh"] is False  # a quiet check must not poke the board
    assert rt.changes_pending == 3 and "drift" in log.open
    assert rt.orch.claims == []  # checking the mapping sends nothing to the board
    B.plan = Obj(actions=[], slots={0: Obj(serial="aaaa0001", label="Kitchen Pico"), 1: Obj(serial="aaaa0002", label="Hall Pico")})
    asyncio.run(rt._check_drift())
    assert rt.changes_pending == 0 and "drift" not in log.open


def test_a_failing_drift_check_never_breaks_anything(monkeypatch):
    rt, _ = per_pico_runtime(monkeypatch, {})
    rt._lock = asyncio.Lock()
    rt.orch.devices = [{"ieee_address": "0xabc"}]

    async def boom(refresh=True):
        raise RuntimeError("registry unavailable")

    rt._compute = boom
    asyncio.run(rt._check_drift())
    assert rt.changes_pending is None


def test_the_drift_check_waits_until_the_bridge_is_known():
    rt = make_runtime({}, set(), bridge=None)
    called = []

    async def compute(refresh=True):
        called.append(1)

    rt._compute = compute
    asyncio.run(rt._check_drift())
    assert called == []


def test_applying_remembers_which_automations_feed_which_pico_and_their_names(monkeypatch):
    async def go():
        rt = flow_runtime(monkeypatch)
        monkeypatch.setattr(importlib.import_module("lutbridge.runtime"), "ir", IssueLog())
        build = await rt.async_plan()
        await rt.async_apply(build.plan_id)
        saved = rt.store.saved
        assert saved["sources_by_serial"] == {"059582be": ["Lutron Pico - Living Room Lights"]}
        assert saved["names"] == {"059582be": "Living Room Pico"}
        assert rt.sources_by_serial == {"059582be": {"Lutron Pico - Living Room Lights"}} and rt.changes_pending == 0
    asyncio.run(go())


def test_an_up_to_date_mapping_upgrades_state_saved_before_sources_were_tracked_per_pico(monkeypatch):
    async def go():
        rt = flow_runtime(monkeypatch)
        monkeypatch.setattr(importlib.import_module("lutbridge.runtime"), "ir", IssueLog())
        await rt.async_apply((await rt.async_plan()).plan_id)
        rt.orch.devices = [{"ieee_address": "0xabc"}]
        living = "Lutron Pico - Living Room Lights"
        # state as an older version saved it: the automations as one set, nothing per Pico, no names
        rt.sources_by_serial, rt.names, rt.sources = {}, {}, {living}
        rt.store.saved = None
        await rt._check_drift()
        assert rt.sources_by_serial == {"059582be": {living}} and rt.names == {"059582be": "Living Room Pico"}
        assert rt.store.saved["sources_by_serial"] == {"059582be": [living]}  # saved, so it survives a restart
        rt.store.saved = None
        await rt._check_drift()
        assert rt.store.saved is None  # nothing changed, so nothing is written again
    asyncio.run(go())


def test_a_renamed_automation_is_followed_when_the_mapping_is_unchanged(monkeypatch):
    async def go():
        rt = flow_runtime(monkeypatch)
        monkeypatch.setattr(importlib.import_module("lutbridge.runtime"), "ir", IssueLog())
        await rt.async_apply((await rt.async_plan()).plan_id)
        rt.orch.devices = [{"ieee_address": "0xabc"}]
        rt.sources_by_serial = {"059582be": {"An automation that was renamed"}}
        rt.sources = {"An automation that was renamed"}
        await rt._check_drift()
        assert rt.sources_by_serial == {"059582be": {"Lutron Pico - Living Room Lights"}}
    asyncio.run(go())


def test_sources_are_not_adopted_while_changes_are_pending(monkeypatch):
    async def go():
        rt = flow_runtime(monkeypatch)
        monkeypatch.setattr(importlib.import_module("lutbridge.runtime"), "ir", IssueLog())
        rt.orch.devices = [{"ieee_address": "0xabc"}]  # nothing applied yet, so the plan has work to do
        await rt._check_drift()
        assert rt.changes_pending and rt.sources_by_serial == {} and rt.store.saved is None
    asyncio.run(go())


def test_health_entities_read_the_runtime(monkeypatch):
    rt, _ = per_pico_runtime(monkeypatch, {"Kitchen": "on", "Hall": "off"})
    asyncio.run(rt._beat())
    bs = importlib.import_module("lutbridge.binary_sensor")
    sn = importlib.import_module("lutbridge.sensor")
    problem = bs.Problem.__new__(bs.Problem)
    problem.runtime = rt
    assert problem.is_on is True and problem.extra_state_attributes["keys"] == ["on_fallback"]
    health_sensor = sn.HealthSensor.__new__(sn.HealthSensor)
    health_sensor.runtime = rt
    assert "Hall Pico" in health_sensor.native_value and health_sensor.extra_state_attributes["board"] == "online"
    fallback = sn.OnFallbackSensor.__new__(sn.OnFallbackSensor)
    fallback.runtime = rt
    assert fallback.native_value == 1 and "Hall Pico" in fallback.extra_state_attributes["picos"]
    running = bs.HeartbeatOk.__new__(bs.HeartbeatOk)
    running.runtime = rt
    assert running.is_on is True  # claims are still being sent for the other Pico


def test_remembered_slots_survive_a_restart_through_the_store(monkeypatch):
    async def go():
        rt = flow_runtime(monkeypatch)
        build = await rt.async_plan()
        await rt.async_apply(build.plan_id)
        restarted = flow_runtime(monkeypatch)
        restarted.orch = rt.orch  # the world kept its groups and binds
        saved = rt.store.saved
        restarted.applied_slots = {int(i): (v[0], v[1]) for i, v in saved["slots"].items()}
        assert (await restarted.async_plan()).plan.empty
    asyncio.run(go())
