# lutbridge-ha

A Home Assistant integration that gives your Lutron Pico remotes a fallback. It works out which Zigbee lights each
Pico controls from the automations you already have, programs that into a small ESP32 bridge, and keeps the bridge
quiet while Home Assistant is looking after the Picos. If Home Assistant, MQTT or a Pico's automation stops working,
the bridge takes over those Picos and switches their lights directly over Zigbee.

It is the companion to **[lutbridge-esp32](https://github.com/davemhammer/lutbridge-esp32)**, the bridge itself: the
firmware, the wiring, the radio and the command protocol live there. You need both.

Nothing is configured by hand. The integration makes a plan, shows it to you in plain language, and changes nothing
on your Zigbee network until you approve it.

## How it works

```
 Pico ──433 MHz──> lutbridge-esp32 ──Zigbee group──> lights          (only when Home Assistant is not handling it)
                       ^
                       | claim: "these Picos are handled, stay quiet", every 15 s
              lutbridge-ha ──MQTT──> Zigbee2MQTT
```

- It **derives** the mapping: for each Lutron Caseta Pico it reads your automations (device triggers that turn lights
  on, off or toggle, and the `smartqasa/lutron_pico_5_light` blueprint or its 2-button counterpart
  `lutron_pico_2_light`) and works out the Zigbee lights behind them, expanding light groups.
- It **plans** the change: a Zigbee2MQTT group per Pico (named `lutbridge_<pico name>`, or an existing group with
  exactly the right lights), a slot in the bridge, and a bind. You review and approve it.
- It **claims** for the Picos Home Assistant is handling, every 15 s. A claim lasts 45 s. A Pico whose source
  automation is missing or switched off is left out of the claim, so the bridge serves that Pico alone while every
  other Pico stays with Home Assistant.
- It **reports** its own health, so you find out about holes in the protection before an outage does.

More detail: [`docs/architecture.md`](docs/architecture.md). The bridge's side: the
[protocol](https://github.com/davemhammer/lutbridge-esp32/blob/main/docs/protocol.md).

## What you need

- The bridge: an ESP32-C6 with a CC1101, built and flashed from
  [lutbridge-esp32](https://github.com/davemhammer/lutbridge-esp32) and paired with Zigbee2MQTT (model
  `l-re.pico-bridge`). Firmware 0.2 or later is needed for per-Pico claims; any version works otherwise.
- Home Assistant 2025.1 or later with the MQTT integration, Zigbee2MQTT, and the Lutron Caseta integration for your
  Picos.
- Lights that are Zigbee2MQTT devices. Lights on other protocols are reported and skipped; they get no fallback.

## Install

**With HACS:** HACS, three-dot menu, Custom repositories, add `https://github.com/davemhammer/lutbridge-ha` with
category *Integration*, then download **Lutron Pico Fallback Bridge** and restart Home Assistant.

**By hand:** copy `custom_components/lutbridge` to `<config>/custom_components/lutbridge` and restart.

Then add **Lutron Pico Fallback Bridge** under Settings, Devices & services. It installs the bundled Zigbee2MQTT
converter itself the first time it sees the paired bridge, and again whenever the bundled converter changes.

**Recommended:** turn on Zigbee2MQTT's availability feature (`availability: true`). Without it the integration cannot
tell whether the bridge is online and reports that as "unknown" rather than guessing.

**Updating:** update the integration and, when a release says so, reflash the bridge's firmware. The integration
works with old firmware; per-Pico claims need firmware 0.2. If the firmware is too old to leave one Pico out of a
claim, the integration notices, claims nothing while any Pico needs the fallback (so the bridge arms for all of them,
the safe direction), and raises a Repairs issue telling you to update it.

## Provision

Press **Make plan** (or call `lutbridge.plan`). Review the result, then press **Apply plan**. Or open **Configure** on
the integration, which shows the plan one Pico at a time and applies from there.

## Using it

**Review.** The plan lists every Pico with its lights, whether it is new, changed, unchanged or no longer covered,
which Picos, buttons or automations could not be covered and why, and a count of operations. Nothing on your network
changes until you approve. Groups the integration creates are named `lutbridge_<pico name>` and only those are ever
changed or removed; groups you made yourself are reused when they have exactly the right members and are otherwise
left alone.

The same review text is the `review` attribute of the Plan sensor, for a dashboard:

```yaml
type: markdown
title: Pico fallback plan
content: "{{ state_attr('sensor.lutron_pico_fallback_plan', 'review') }}"
```

**Entities.**

| Entity | Meaning |
|---|---|
| Plan | `up to date`, or how many changes are pending; the `review` attribute has the readable plan |
| Heartbeat | `sending`, `sending; fallback active for <Pico>`, or `withheld: ...` |
| Heartbeat running | on while claims are being sent for at least one Pico |
| Last claim sent | time of the last claim |
| Picos provisioned | how many Picos have a fallback |
| Picos on fallback | how many Picos are being served by the bridge right now, with the reasons as attributes |
| Health | `ok`, or what is wrong; the attributes list every problem and the bridge's availability |
| Problem | on when anything in the Health sensor is wrong; use it to notify yourself |
| Make plan, Apply plan | buttons |

**Health and Repairs.** Anything that leaves Picos unprotected is raised in Settings, Repairs, and clears itself when
fixed: the bridge is not paired or reported offline by Zigbee2MQTT, Zigbee2MQTT itself is offline, the heartbeat
stopped, the converter is out of date, the firmware cannot claim per Pico, the mapping no longer matches Home
Assistant (a new Pico, a changed light group), or a Pico is being served by the fallback because its automation is
missing or off. The mapping is re-checked quietly every 10 minutes. The check changes nothing; apply a new plan to fix
a stale mapping.

**Services.**

| Service | Purpose |
|---|---|
| `lutbridge.plan` | Compute and show the plan. `rewrite_slots: true` ignores what was remembered and rewrites every slot. |
| `lutbridge.apply` | Approve and apply the plan last shown (`plan_id` optional; must match if given). Refuses if anything changed since. |
| `lutbridge.install_converter` | Send the bundled Zigbee2MQTT converter to Zigbee2MQTT. |

**Slot read-back.** The bridge's slot table cannot be read back through Zigbee2MQTT, so the integration remembers
the table it last applied and plans against that. After erasing or reflashing the bridge, plan with
`rewrite_slots: true`.

**Testing the fallback.** Switch off one automation that a Pico drives. Within 15 s the Heartbeat sensor reads
`sending; fallback active for <that Pico>` and, 45 s after that Pico's last claim, the bridge arms for that Pico
only; pressing it now controls its lights through the bridge alone, while every other Pico still goes through Home
Assistant. Switch the automation back on and the next claim hands the Pico back. A Pico with several automations (one
per button) is handed to the fallback if any of them is missing or off. While a Pico is on the fallback, Home
Assistant's own automations for it (if any still run) act too; on/off is harmless twice and a dim may stutter.

## Limits

- **Coverage:** only Picos whose automations turn Zigbee2MQTT lights on, off or toggle are provisioned. Scenes, scripts
  and non-light actions are listed as not covered. Automation conditions are ignored on purpose: the fallback is plain
  on/off/dim. The favorite button is not mapped.
- **Capacity:** 24 slots (one per Pico by default; a Pico whose buttons go to different groups uses more).
- **Bridge health:** the bridge sends nothing on its own that Home Assistant can poll, so its liveness comes from
  Zigbee2MQTT's availability feature. Without it, an unplugged bridge is not noticed until a Pico stops working.
- **Press timing:** Home Assistant reacts to the Pico on release; the bridge acts on the press itself.

## Development

```
python3 -m pytest        # integration logic and Home Assistant glue, no Home Assistant needed
```

The logic (`custom_components/lutbridge/core`) is pure Python with no Home Assistant imports and is tested against a
fake Zigbee2MQTT. The Home Assistant glue is tested with stand-ins for Home Assistant, which cannot catch everything:
the real-instance behaviours that matter (callbacks arriving on a worker thread, device identifiers that are not
pairs, a parameter shadowing the `callback` decorator) were each found only by running it. Keep tests for the
adapter layer (`hamqtt.py`) and the registry reads (`collect.py`).

The Zigbee2MQTT converter in `custom_components/lutbridge/pico_bridge.mjs` is owned by lutbridge-esp32 and is copied
here unchanged; CI fails if the copy drifts from that repository.

## Layout

- `custom_components/lutbridge/core/`: `pico`, `derive`, `resolve`, `planner`, `pipeline`, `heartbeat`, `health`,
  `manager`, `z2m`.
- `custom_components/lutbridge/`: the Home Assistant glue (`__init__`, `config_flow`, `runtime`, `collect`, `hamqtt`,
  entities, services) and the bundled converter.
- `tests/`: unit tests, a fake Zigbee2MQTT, a glue smoke test, and packaging checks (HACS and hassfest requirements).

## Notes

- A plain MQTT subscription callback runs in a worker thread unless decorated with `@callback`; touching asyncio
  objects there fails with "no running event loop". The orchestrator also hops every handler onto its event loop
  whichever thread delivers the message.
- Zigbee2MQTT registers a device's custom cluster when it starts and does not re-register it when a converter file is
  reloaded, so a command added by a newer converter fails with `UNSUP_COMMAND` until it restarts. The converter
  registers its definition again before each use, which makes updates take effect at once. If you ever see that error
  for a command that should exist, restart Zigbee2MQTT.
- Home Assistant device identifiers are not always pairs; read them defensively.

## License

Copyright 2026 Dave Hammer. Licensed under the [Apache License, Version 2.0](LICENSE); see [NOTICE](NOTICE). Every
source file carries an `SPDX-License-Identifier` header.

Lutron, Caseta, Pico and Clear Connect are trademarks of Lutron Electronics Co., Inc.; Zigbee is a trademark of the
Connectivity Standards Alliance. This project is independent and is not affiliated with, endorsed by, or supported by
Lutron, the Connectivity Standards Alliance, Home Assistant, or Zigbee2MQTT. The names are used only to say what it works
with. Use at your own risk: it switches lights and is meant as a fallback, not as a safety system.
