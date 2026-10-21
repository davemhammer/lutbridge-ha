# Architecture

How the integration works. The bridge it drives (firmware, radio, the slot table and the command protocol) is
documented in [lutbridge-esp32](https://github.com/davemhammer/lutbridge-esp32); the commands used here are
described in its [protocol](https://github.com/davemhammer/lutbridge-esp32/blob/main/docs/protocol.md).

## Goals

- Zero manual mapping: derive it from what Home Assistant already knows, deterministically. It does not call a
  language model, so it keeps working with nobody at the keyboard.
- Nothing changes on the live Zigbee network without the owner's approval, and only what the integration created is
  ever removed.
- Know when the protection has a hole in it, and say so.

## Layout

`core/` is pure Python with no Home Assistant imports (`pico`, `derive`, `resolve`, `planner`, `pipeline`,
`heartbeat`, `health`, `manager`, `z2m`). The files beside it are thin glue (`__init__`, `config_flow`, `runtime`,
`collect`, `hamqtt`, entities and services). The glue reads Home Assistant and hands plain data to the core, so the
logic is tested against a fake Zigbee2MQTT without Home Assistant.

## The pipeline

1. **Discover**: every `lutron_caseta` Pico device. Home Assistant names a Pico by the decimal form of its 32-bit
   serial, so the on-air serial is that number in hex (identifier `93694086` is `0595a886`). The device model gives
   the button layout. Identifiers are not always two-element tuples; read them defensively.
2. **Derive targets** from existing automations, by fixed rules:
   - R1: a device trigger on a Pico (`device_id`, `subtype`, `type`) whose actions call `light.turn_on`,
     `light.turn_off` or `light.toggle` on light entities maps that button to those lights.
   - R2: the `smartqasa/lutron_pico_5_light` blueprint (`pico` plus `entity` list) maps all four dimming buttons to
     those lights; the 2-button `lutron_pico_2_light` blueprint maps on and off. Blueprints are matched by file name,
     whatever folder they are installed under.
   - Anything else is listed as not covered, with the reason. Automation conditions are ignored on purpose (the
     fallback is plain on/off/dim) and flagged in the plan.

   Adding a rule means adding a function to `derive.py` and fixtures to its tests; nothing else changes.
3. **Resolve** target lights to Zigbee2MQTT devices, expanding light groups and nested groups. Lights that are not
   Zigbee2MQTT devices are reported and skipped.
4. **Plan**: find or create a Zigbee2MQTT group with exactly each Pico's member set (`lutbridge_<pico name>`),
   reusing a matching group (any name) when one exists. Slots are assigned in serial order so an unchanged system
   gives an identical plan. Only groups whose names start with `lutbridge_` are ever removed or emptied. The plan is a
   diff of slots, groups and binds, with a stable id, shown for approval.
5. **Apply** in the safe order: unbind, clear/set slots, bind. A crash leaves a button unbound, never aimed at the
   wrong group. Requests go through the Zigbee2MQTT bridge API with transaction ids and are paced; commands to the
   bridge set `disableDefaultResponse`.
6. **Remember**: the applied slot table is stored in Home Assistant, because the bridge's slots cannot be read back
   through Zigbee2MQTT. Planning compares against that; when it is missing, every slot is cleared and rewritten.
7. **Run**: the heartbeat, and the health checks, below.

## The heartbeat

The integration sends a claim every 15 s and immediately when the bridge re-announces after a reboot. Each claim
disarms the slots it names for 45 s, so up to two can be lost without the bridge arming.

It leaves a Pico out of the claim when any automation its mapping was derived from is missing or switched off, so
that Pico is served by the fallback rather than left with dead buttons, and every other Pico stays with Home
Assistant. The automations are remembered per Pico serial (`sources_by_serial`) with the applied slots. A mapping saved
before that was tracked falls back to the first version's rule: any problem withholds the whole claim. If every Pico
is left out there is nothing to claim and no claim is sent.

- **Which command.** Claiming every Pico is sent as the plain 1-byte `claim`, which every firmware version accepts.
  Claiming only some uses `claimSlots`, new in firmware 0.2.
- **Old firmware.** It answers `claimSlots` with `UNSUP_COMMAND`, which Zigbee2MQTT logs and publishes on
  `<base>/bridge/logging`. The integration watches for that line and, from then on, treats a partial claim as "claim
  nothing" (the first version's rule, which arms the bridge for every Pico, the safe direction) and raises a Repairs
  issue. A reboot announcement from the bridge clears the flag so a reflashed bridge is tried again.
- **Converter updates.** Zigbee2MQTT registers a device's custom cluster once, when it starts, and does not re-register
  it when a converter file is reloaded; a newly added command would fail with `UNSUP_COMMAND` until a restart. The
  converter registers its cluster definition again before each use, so reinstalling it takes effect at once.

## Health

`core/health.py` turns plain inputs into a verdict, so it is tested without Home Assistant. The inputs are things the
integration already knows or Zigbee2MQTT publishes: whether the bridge is in the device list, Zigbee2MQTT's
`bridge/state`, the bridge's `availability` topic, whether claims are going out and how old the last one is, whether
Zigbee2MQTT runs the bundled converter, whether the firmware supports per-Pico claims, how many operations a fresh plan
would need, and which Picos are on the fallback. Each problem has a stable key, a sentence, and a Repairs issue that is
raised once and cleared automatically.

- **Why availability and not a poll.** The bridge has no way to answer a request that Home Assistant can read:
  Zigbee2MQTT never delivers its attribute read-backs. Its liveness therefore comes from Zigbee2MQTT's availability
  feature (it pings routers). With the feature off the bridge is reported `unknown`, which is neither a problem nor a
  claim of health.
- **Drift check.** Every 10 minutes the integration plans quietly (no bridge traffic, nothing shown, nothing applied)
  and records how many changes the plan would need. A non-zero count raises "mapping out of date".
- **Severity.** The bridge missing, offline, or the heartbeat stopped leave Picos unprotected and are errors; the rest
  are warnings.

## Threading

Home Assistant runs a plain MQTT subscription callback in a worker thread. The adapter (`hamqtt.py`) decorates its
callback with `@callback` so it runs on the event loop, and the orchestrator additionally hops every handler onto its
own loop, so it is correct whichever thread delivers a message. Do not name a function parameter `callback` in a
module that imports the decorator.

## Operating it

`Make plan` (button or `lutbridge.plan`) computes the plan and shows it as a notification, a Plan sensor attribute and
the Configure dialog, all with the same readable per-Pico text. `Apply plan` (button, `lutbridge.apply`, or the
Configure dialog's **Apply these changes** switch) is the approval: it recomputes, refuses if anything changed since
the plan was shown, applies in order, and records what it applied. The converter is installed over MQTT when the
bridge is first seen unsupported by Zigbee2MQTT, and again whenever the bundled converter's content hash changes.

## Possible extensions

- Per-Pico approval (apply some Picos' changes and not others).
- Re-planning automatically when Home Assistant changes, with the same approval step.
- Further derivation rules (scenes, scripts, other blueprints), and one slot per button for Picos whose buttons control
  different lights.
- Reading slot state back, if Zigbee2MQTT can be made to report the `slots` attribute.
- A favorite-button design.
- Discovering Lutron dimmers, if the bridge ever presents them as Zigbee dimmers (see the lutbridge-esp32 repository).
