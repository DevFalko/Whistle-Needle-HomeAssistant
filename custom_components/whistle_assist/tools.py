# SPDX-License-Identifier: Apache-2.0
"""The small, fixed Needle toolset.

Needle 3 reads the whole toolset as part of a 4096-token prompt and works best
with five tools or fewer. The previous design put every exposed entity into
enums and descriptions, which made `needle_init` fail on real installations.
Here the tools stay generic: the model copies the device words from the
request, and `matcher.resolve` maps them to entities afterwards. Only the
area names are an enum, because they are few and closed.
"""

from __future__ import annotations

from typing import Any

CONTROL_ACTIONS = ["on", "off", "toggle", "open", "close", "stop"]
LEVEL_SETTINGS = ["brightness", "temperature", "position", "volume", "speed"]
MEDIA_ACTIONS = ["play", "pause", "next", "previous", "mute", "unmute"]

# Keep the enum (and therefore the prompt) bounded on very large homes.
MAX_AREAS = 40


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


def build_tools(areas: list[str]) -> list[dict[str, Any]]:
    """Return the five Needle tools for the given area names."""
    area_names = sorted(set(areas))[:MAX_AREAS]
    device = {
        "type": "string",
        "description": "The device exactly as the user named it, e.g. 'Deckenlampe', 'Licht', 'Rollo', 'Fernseher'.",
    }
    area: dict[str, Any] = {
        "type": "string",
        "description": "The room the user named; omit when no room is said.",
    }
    if area_names:
        area["enum"] = area_names

    return [
        _schema(
            "control_device",
            "Switch a device on or off, toggle it, or open, close or stop a blind, shutter or garage door. "
            "Use for lights, plugs, fans, heating, TVs and blinds. Never use for scenes.",
            {
                "action": {"type": "string", "enum": CONTROL_ACTIONS, "description": "on, off, toggle, open, close or stop."},
                "device": device,
                "area": area,
            },
            ["action", "device"],
        ),
        _schema(
            "set_level",
            "Set a numeric level: light brightness in percent, target temperature in degrees, "
            "blind position in percent, speaker volume in percent, or fan speed in percent.",
            {
                "setting": {"type": "string", "enum": LEVEL_SETTINGS, "description": "Which level to set."},
                "value": {"type": "number", "minimum": 0, "maximum": 100, "description": "The number the user said."},
                "device": device,
                "area": area,
            },
            ["setting", "value", "device"],
        ),
        _schema(
            "get_state",
            "Answer a question about the current state of a device or sensor: is it on, is the window open, "
            "what is the temperature or humidity.",
            {"device": device, "area": area},
            ["device"],
        ),
        _schema(
            "activate_scene",
            "Activate a named scene or mood, e.g. 'Kino', 'Gute Nacht', 'Entspannen'.",
            {"scene": {"type": "string", "description": "The scene name as the user said it."}},
            ["scene"],
        ),
        _schema(
            "control_media",
            "Play, pause, skip forward or back, mute or unmute music, radio or a TV.",
            {
                "action": {"type": "string", "enum": MEDIA_ACTIONS, "description": "The playback action."},
                "device": device,
                "area": area,
            },
            ["action"],
        ),
    ]


SYSTEM_PROMPT = (
    "You control a smart home. Pick a tool only when the request is a direct device command or a "
    "device status question. Copy device and scene names from the request verbatim, in the user's "
    "language. For anything else (news, web search, general questions, chit-chat, timers, shopping) "
    "call no tool."
)
