# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
DOMAIN = "lutbridge"
CONF_BASE = "base_topic"
DEFAULT_BASE = "zigbee2mqtt"
PLATFORMS = ["binary_sensor", "sensor", "button"]
SIGNAL_UPDATE = f"{DOMAIN}_update"
HEARTBEAT_SECONDS = 15
STORE_VERSION = 1
NOTIFICATION_ID = f"{DOMAIN}_plan"
DRIFT_CHECK_SECONDS = 600  # how often to re-plan quietly to notice that the mapping went out of date
FIRST_DRIFT_CHECK_SECONDS = 90  # after startup, once the Zigbee2MQTT snapshots have arrived
BRIDGE_REPO_URL = "https://github.com/davemhammer/lutbridge-esp32"  # firmware, wiring, protocol
