# Changelog

## 0.3.0

- **2-button blueprint.** Automations built on the `lutron_pico_2_light` blueprint (on and off for a 2-button Pico)
  are now derived like the 5-button one, instead of being reported as not understood.

## 0.2.0

- **Own repository.** The integration now lives here, apart from the bridge firmware
  ([lutbridge-esp32](https://github.com/davemhammer/lutbridge-esp32)). The two are versioned independently and
  speak a small, additive protocol.
- **Per-Pico arming.** The heartbeat now claims each Pico separately. A Pico whose source automation is missing or
  switched off is left to the fallback after 45 s, and every other Pico stays with Home Assistant. Previously one
  broken automation armed the bridge for all of them. Claiming every Pico is sent as the plain claim that every
  firmware accepts; claiming only some needs bridge firmware 0.2 (`claimSlots`).
- **Old firmware is detected.** If the bridge refuses a per-Pico claim, the integration falls back to the first
  version's rule (claim nothing while any Pico needs the fallback, so the bridge arms for all of them), and raises a
  Repairs issue asking for a firmware update. A reboot of the bridge makes it try again.
- **Upgrades itself.** State saved by 0.1 did not record which automations feed which Pico, so a broken automation
  would have been blamed on every Pico. When the mapping is up to date, the quiet check now records it (and follows an
  automation that was renamed); no re-apply is needed. Found by testing on a live system.
- **Health.** New Problem binary sensor, Health sensor and "Picos on fallback" sensor, and Repairs issues for an
  offline bridge, a stopped heartbeat, an outdated converter, outdated firmware, a mapping that no longer matches Home
  Assistant, and Picos running on the fallback. The mapping is re-checked quietly every 10 minutes. The bridge's
  availability comes from Zigbee2MQTT's availability feature and is reported as unknown when that is off.
- **Converter.** Zigbee2MQTT does not re-register a device's custom cluster when a converter file is reloaded, so a
  newly added command failed with `UNSUP_COMMAND` until Zigbee2MQTT restarted. The converter now registers its
  cluster definition again before each use.
- HACS packaging: `hacs.json`, validation and test workflows, brand icon, documentation.
- Fixes found by running in a live Home Assistant: MQTT callbacks arrive on a worker thread, and a parameter named
  `callback` shadowed the decorator.

## 0.1.0

First working version: derives the Pico to light mapping from existing automations, plans and applies it through
Zigbee2MQTT, sends the heartbeat, installs the converter, and offers a review and approval dialog.
