# SPDX-License-Identifier: Apache-2.0
"""Whistle Assist integration for Home Assistant."""

from __future__ import annotations

import os

# The Cactus runtime enables anonymous usage telemetry by default. Respect an
# explicit operator setting; if there is none, opt out for Home Assistant.
_TELEMETRY_VARS = ("CACTUS_TELEMETRY", "NEEDLE_TELEMETRY", "DO_NOT_TRACK")
if not any(var in os.environ for var in _TELEMETRY_VARS):
    os.environ["CACTUS_TELEMETRY"] = "0"
    os.environ["NEEDLE_TELEMETRY"] = "0"
    os.environ["DO_NOT_TRACK"] = "1"

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .agent import WhistleAssistManager
from .const import CONF_CONFIDENCE_THRESHOLD, DEFAULT_CONFIDENCE_THRESHOLD, DOMAIN

PLATFORMS: list[Platform] = [Platform.CONVERSATION, Platform.STT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Whistle Assist platforms."""
    manager = WhistleAssistManager(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    if manager.preload:
        entry.async_create_background_task(hass, manager.async_preload(), "whistle_assist_preload")
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 -> v2: the old default threshold 0.70 sent almost every German command away."""
    if entry.version == 1:
        def fix(values: dict) -> dict:
            values = dict(values)
            if float(values.get(CONF_CONFIDENCE_THRESHOLD, 0.7)) == 0.7:
                values[CONF_CONFIDENCE_THRESHOLD] = DEFAULT_CONFIDENCE_THRESHOLD
            return values

        hass.config_entries.async_update_entry(
            entry, data=fix(entry.data), options=fix(entry.options) if entry.options else {}, version=2
        )
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Whistle Assist platforms."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
