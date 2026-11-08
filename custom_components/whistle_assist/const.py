# SPDX-License-Identifier: Apache-2.0
"""Constants for Whistle Assist."""

from typing import Final

DOMAIN: Final = "whistle_assist"
CONF_LANGUAGE: Final = "language"
CONF_CONFIDENCE_THRESHOLD: Final = "confidence_threshold"
CONF_FALLBACK_AGENT: Final = "fallback_agent"
CONF_USE_HA_INTENTS: Final = "use_ha_intents"
CONF_PRELOAD: Final = "preload_models"

DEFAULT_LANGUAGE: Final = "auto"
# Needle's confidence on correct German device commands is often 0.15-0.35.
DEFAULT_CONFIDENCE_THRESHOLD: Final = 0.10
DEFAULT_USE_HA_INTENTS: Final = True
DEFAULT_PRELOAD: Final = True
NO_FALLBACK: Final = ""

SUPPORTED_LANGUAGES: Final = ["en", "de", "fr", "es", "it", "nl", "pl"]

# Domains whose state may be read through the get_state tool.
READABLE_DOMAINS: Final = {
    "light", "switch", "cover", "climate", "fan", "media_player", "scene",
    "vacuum", "humidifier", "water_heater", "sensor", "binary_sensor",
    "person", "device_tracker", "weather", "lock", "input_boolean",
    "input_number", "input_select", "number", "select", "todo",
}

# Domains the control_device tool may change.
CONTROLLABLE_DOMAINS: Final = {
    "light", "switch", "input_boolean", "fan", "cover", "climate",
    "media_player", "vacuum", "humidifier", "water_heater",
}

# Minimum fuzzy score (0..1) for a spoken device name to resolve to an entity.
MATCH_THRESHOLD: Final = 0.62
