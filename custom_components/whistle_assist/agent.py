# SPDX-License-Identifier: Apache-2.0
"""Local Needle/Whistle runtime and guarded Home Assistant service calls."""

from __future__ import annotations

import array
import asyncio
from dataclasses import dataclass
import json
import logging
import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_CONFIDENCE_THRESHOLD,
    CONF_FALLBACK_AGENT,
    CONF_LANGUAGE,
    CONF_PRELOAD,
    CONF_USE_HA_INTENTS,
    CONTROLLABLE_DOMAINS,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_LANGUAGE,
    DEFAULT_PRELOAD,
    DEFAULT_USE_HA_INTENTS,
    MATCH_THRESHOLD,
    NO_FALLBACK,
    READABLE_DOMAINS,
)
from .matcher import (
    Candidate,
    MatchResult,
    best_area,
    infer_action,
    infer_measure,
    is_question,
    resolve,
)
from .tools import SYSTEM_PROMPT, build_tools

_LOGGER = logging.getLogger(__name__)
MEASURE_CLASSES = {"temperature": "temperature", "humidity": "humidity", "power": "power", "battery": "battery"}
EXPOSED_ASSISTANT = "conversation"
MAX_KEYWORD_CHARS = 2000

_TEXT = {
    "de": {
        "on": "{name} ist an.",
        "off": "{name} ist aus.",
        "toggle": "{name} umgeschaltet.",
        "open": "{name} wird geöffnet.",
        "close": "{name} wird geschlossen.",
        "stop": "{name} gestoppt.",
        "brightness": "{name} auf {value} Prozent.",
        "temperature": "{name} auf {value} Grad.",
        "position": "{name} auf {value} Prozent.",
        "volume": "Lautstärke von {name} auf {value} Prozent.",
        "speed": "{name} auf {value} Prozent.",
        "scene": "Szene {name} aktiviert.",
        "play": "{name} spielt.",
        "pause": "{name} pausiert.",
        "next": "Nächster Titel auf {name}.",
        "previous": "Vorheriger Titel auf {name}.",
        "mute": "{name} stummgeschaltet.",
        "unmute": "{name} wieder laut.",
        "state": "{name}: {state}.",
        "group_state_on": "An: {names}.",
        "group_state_none": "Alles aus.",
        "failed": "Das hat nicht geklappt: {name}.",
        "out_of_range": "{value} liegt außerhalb des erlaubten Bereichs für {name}.",
        "no_local": "Das kann ich lokal nicht erledigen.",
        "states": {"on": "an", "off": "aus", "open": "offen", "closed": "geschlossen",
                   "opening": "öffnet", "closing": "schließt", "playing": "spielt",
                   "paused": "pausiert", "idle": "bereit", "home": "zu Hause",
                   "not_home": "unterwegs", "unavailable": "nicht erreichbar",
                   "unknown": "unbekannt", "heat": "heizt", "cleaning": "saugt",
                   "docked": "in der Ladestation"},
    },
    "en": {
        "on": "{name} is on.",
        "off": "{name} is off.",
        "toggle": "Toggled {name}.",
        "open": "Opening {name}.",
        "close": "Closing {name}.",
        "stop": "Stopped {name}.",
        "brightness": "{name} set to {value} percent.",
        "temperature": "{name} set to {value} degrees.",
        "position": "{name} set to {value} percent.",
        "volume": "{name} volume set to {value} percent.",
        "speed": "{name} set to {value} percent.",
        "scene": "Activated {name}.",
        "play": "Playing on {name}.",
        "pause": "Paused {name}.",
        "next": "Next track on {name}.",
        "previous": "Previous track on {name}.",
        "mute": "Muted {name}.",
        "unmute": "Unmuted {name}.",
        "state": "{name} is {state}.",
        "group_state_on": "On: {names}.",
        "group_state_none": "Everything is off.",
        "failed": "That did not work: {name}.",
        "out_of_range": "{value} is outside the allowed range for {name}.",
        "no_local": "I can't handle that locally.",
        "states": {"not_home": "away"},
    },
}


def _text(language: str | None) -> dict[str, Any]:
    return _TEXT["de"] if (language or "").lower().startswith("de") else _TEXT["en"]


@dataclass
class LocalOutcome:
    """Result of the local Needle stage."""

    handled: bool
    speech: str = ""
    reason: str = ""


class Unresolved(Exception):
    """The model picked a tool but the target could not be resolved safely."""


class WhistleAssistManager:
    """Shared runtime for the conversation and STT platforms."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._agent: Any | None = None
        self._signature: str | None = None
        # One engine serves Needle and Whistle and it is not thread-safe.
        self.engine_lock = asyncio.Lock()
        # Our conversation id -> conversation id of the fallback agent.
        self.fallback_conversations: dict[str, str] = {}

    # ------------------------------------------------------------------ options
    def _opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, self.entry.data.get(key, default))

    @property
    def language(self) -> str:
        return str(self._opt(CONF_LANGUAGE, DEFAULT_LANGUAGE))

    @property
    def confidence_threshold(self) -> float:
        try:
            return min(1.0, max(0.0, float(self._opt(CONF_CONFIDENCE_THRESHOLD, DEFAULT_CONFIDENCE_THRESHOLD))))
        except (TypeError, ValueError):
            return DEFAULT_CONFIDENCE_THRESHOLD

    @property
    def fallback_agent(self) -> str | None:
        value = self._opt(CONF_FALLBACK_AGENT, NO_FALLBACK)
        return str(value) if value else None

    @property
    def use_ha_intents(self) -> bool:
        return bool(self._opt(CONF_USE_HA_INTENTS, DEFAULT_USE_HA_INTENTS))

    @property
    def preload(self) -> bool:
        return bool(self._opt(CONF_PRELOAD, DEFAULT_PRELOAD))

    # ----------------------------------------------------------------- entities
    def candidates(self) -> list[Candidate]:
        """Exposed entities with every name a user might say for them."""
        try:
            from homeassistant.components.homeassistant.exposed_entities import async_should_expose
        except ImportError:
            _LOGGER.error("Home Assistant exposed-entity API is unavailable")
            return []

        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        result: list[Candidate] = []
        for state in self.hass.states.async_all():
            domain = state.domain
            if domain not in READABLE_DOMAINS:
                continue
            try:
                if not async_should_expose(self.hass, EXPOSED_ASSISTANT, state.entity_id):
                    continue
            except (KeyError, HomeAssistantError):
                continue
            names = [str(state.attributes.get("friendly_name") or state.entity_id)]
            area_id = None
            if entry := ent_reg.async_get(state.entity_id):
                names += [a for a in (entry.aliases or ()) if isinstance(a, str)]
                if entry.name:
                    names.append(entry.name)
                area_id = entry.area_id
                if entry.device_id and (device := dev_reg.async_get(entry.device_id)):
                    area_id = area_id or device.area_id
            area = area_reg.async_get_area(area_id) if area_id else None
            result.append(
                Candidate(
                    state.entity_id, domain, list(dict.fromkeys(names)), area.name if area else None,
                    state.attributes.get("device_class"),
                )
            )
        return result

    @staticmethod
    def areas(candidates: list[Candidate]) -> list[str]:
        return sorted({c.area for c in candidates if c.area})

    def keywords(self) -> list[str]:
        """Device and room names that Whistle should favour when transcribing."""
        cands = self.candidates()
        words: list[str] = self.areas(cands)
        words += [c.names[0] for c in cands if c.domain in CONTROLLABLE_DOMAINS | {"scene"}]
        out, size = [], 0
        for word in dict.fromkeys(words):
            size += len(word) + 1
            if size > MAX_KEYWORD_CHARS:
                break
            out.append(word)
        return out

    # -------------------------------------------------------------------- model
    async def _get_agent(self, areas: list[str]) -> Any:
        tools = build_tools(areas)
        signature = json.dumps(tools, sort_keys=True)
        if self._agent is None or signature != self._signature:
            def create() -> Any:
                return _engine().Needle(tools=tools, system=SYSTEM_PROMPT, stateless=True)

            self._agent = await self.hass.async_add_executor_job(create)
            self._signature = signature
        return self._agent

    async def async_preload(self) -> None:
        """Download and initialise the models so the first command is fast."""
        try:
            async with self.engine_lock:
                await self._get_agent(self.areas(self.candidates()))
                await self.hass.async_add_executor_job(_warm_whistle)
            _LOGGER.info("Whistle Assist models are ready")
        except Exception:  # noqa: BLE001 - never break setup because of a download
            _LOGGER.exception("Preloading the Needle/Whistle models failed; they load on first use")

    # --------------------------------------------------------------- interpret
    async def async_interpret(self, text: str, language: str | None, context: Context) -> LocalOutcome:
        """Run Needle; return handled=False when the request should go elsewhere."""
        text = text.strip()
        if not text:
            return LocalOutcome(False, reason="empty")
        cands = self.candidates()
        try:
            async with self.engine_lock:
                agent = await self._get_agent(self.areas(cands))
                response = await self.hass.async_add_executor_job(agent.complete, text)
        except Exception as err:  # noqa: BLE001 - model errors must not break Assist
            _LOGGER.exception("Needle inference failed")
            return LocalOutcome(False, reason=f"needle_error:{err.__class__.__name__}")

        _LOGGER.debug("Needle response for %r: %s", text, response)
        if not isinstance(response, dict):
            return LocalOutcome(False, reason="invalid_response")
        calls = response.get("function_calls") or []
        if not calls:
            return LocalOutcome(False, reason="no_tool")
        confidence = response.get("confidence")
        if isinstance(confidence, (int, float)) and float(confidence) < self.confidence_threshold:
            return LocalOutcome(False, reason=f"low_confidence:{float(confidence):.2f}")

        texts = _text(language)
        speech: list[str] = []
        for call in calls:
            try:
                speech.append(await self._execute(call, cands, texts, context, text))
            except Unresolved as err:
                _LOGGER.debug("Could not resolve %s: %s", call, err)
                if not speech:
                    return LocalOutcome(False, reason=f"unresolved:{err}")
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.warning("Executing %s failed: %s", call, err)
                speech.append(texts["failed"].format(name=call.get("name")))
        return LocalOutcome(True, " ".join(speech))

    # ---------------------------------------------------------------- execution
    async def _execute(self, call: dict[str, Any], cands: list[Candidate], texts: dict[str, Any],
                       context: Context, text: str = "") -> str:
        name = call.get("name")
        args = call.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError as err:
                raise Unresolved("arguments") from err
        # Needle sometimes drops or shortens the room ("Nachtlicht in der Küche"
        # -> scene "Nachtlicht"); the user's own sentence fills the gap.
        area = best_area(args.get("area"), text, self.areas(cands))

        if name != "get_state" and is_question(text):
            # "Ist das Licht an?" must never switch the light on.
            raise Unresolved("question routed to a mutating tool")

        if name == "control_device":
            action = args.get("action")
            stated = infer_action(text)
            if stated and stated != action:
                # The sentence says "zu"/"aus" explicitly; Needle sometimes
                # answers "on" or "stop" for those.
                action = stated
            domains = {"cover"} if action in ("open", "close") else set(CONTROLLABLE_DOMAINS)
            match = self._resolve(args.get("device"), area, cands, domains)
            return await self._control(match, action, texts, context)
        if name == "set_level":
            return await self._set_level(args, area, cands, texts, context)
        if name == "get_state":
            args = {**args, "measure": args.get("measure") or infer_measure(text) or "state"}
            return self._get_state(args, area, cands, texts)
        if name == "activate_scene":
            match = self._resolve(args.get("scene"), area, cands, {"scene"})
            ent = match.entities[0]
            await self._call("scene", "turn_on", ent.entity_id, {}, context)
            return texts["scene"].format(name=ent.names[0])
        if name == "control_media":
            return await self._media(args, area, cands, texts, context)
        raise Unresolved(f"unknown tool {name}")

    @staticmethod
    def _resolve(device: Any, area: str | None, cands: list[Candidate], domains: set[str] | None) -> MatchResult:
        match = resolve(str(device or ""), area, cands, domains=domains, threshold=MATCH_THRESHOLD)
        if match is None:
            raise Unresolved(f"no entity for {device!r} in {area!r}")
        return match

    async def _call(self, domain: str, service: str, entity_id: str, data: dict[str, Any],
                    context: Context) -> None:
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity_id, **data}, blocking=True, context=context
        )

    def _label(self, match: MatchResult, area: str | None = None) -> str:
        if len(match.entities) == 1:
            return match.entities[0].names[0]
        return ", ".join(c.names[0] for c in match.entities)

    async def _control(self, match: MatchResult, action: str | None, texts: dict[str, Any],
                       context: Context) -> str:
        if action not in ("on", "off", "toggle", "open", "close", "stop"):
            raise Unresolved(f"action {action!r}")
        failed = []
        for ent in match.entities:
            domain = ent.domain
            if domain not in CONTROLLABLE_DOMAINS:
                raise Unresolved(f"{ent.entity_id} is not controllable")
            service = _service_for(domain, action, self.hass.states.get(ent.entity_id))
            if service is None:
                raise Unresolved(f"{action} not supported for {domain}")
            try:
                await self._call(domain, service, ent.entity_id, {}, context)
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.warning("Service %s.%s for %s failed: %s", domain, service, ent.entity_id, err)
                failed.append(ent.names[0])
        if failed:
            return texts["failed"].format(name=", ".join(failed))
        return texts[action].format(name=self._label(match))

    async def _set_level(self, args: dict[str, Any], area: str | None, cands: list[Candidate],
                         texts: dict[str, Any], context: Context) -> str:
        setting = args.get("setting")
        try:
            value = float(args.get("value"))
        except (TypeError, ValueError) as err:
            raise Unresolved("value") from err
        if not math.isfinite(value):
            raise Unresolved("value")
        domains = {
            "brightness": {"light"}, "temperature": {"climate", "water_heater"},
            "position": {"cover"}, "volume": {"media_player"}, "speed": {"fan"},
        }.get(str(setting))
        if domains is None:
            raise Unresolved(f"setting {setting!r}")
        try:
            match = self._resolve(args.get("device"), area, cands, domains)
        except Unresolved:
            # "Heizung im Wohnzimmer auf 21 Grad" often arrives as device="Wohnzimmer":
            # the setting already fixes the device class, so use all of them in the room.
            group = [c for c in cands if area and c.area == area and c.domain in domains]
            if not group:
                raise
            match = MatchResult(group, 1.0, group=True)
        shown = int(value) if value.is_integer() else value
        for ent in match.entities:
            state = self.hass.states.get(ent.entity_id)
            if setting == "brightness":
                if value <= 0:
                    await self._call("light", "turn_off", ent.entity_id, {}, context)
                else:
                    await self._call("light", "turn_on", ent.entity_id,
                                     {"brightness_pct": int(min(100, max(1, round(value))))}, context)
            elif setting == "temperature":
                low = float((state.attributes.get("min_temp") if state else None) or 5)
                high = float((state.attributes.get("max_temp") if state else None) or 35)
                if not low <= value <= high:
                    return texts["out_of_range"].format(value=shown, name=ent.names[0])
                await self._call(ent.domain, "set_temperature", ent.entity_id, {"temperature": value}, context)
            elif setting == "position":
                await self._call("cover", "set_cover_position", ent.entity_id,
                                 {"position": int(min(100, max(0, round(value))))}, context)
            elif setting == "volume":
                await self._call("media_player", "volume_set", ent.entity_id,
                                 {"volume_level": min(1.0, max(0.0, value / 100))}, context)
            elif setting == "speed":
                await self._call("fan", "set_percentage", ent.entity_id,
                                 {"percentage": int(min(100, max(0, round(value))))}, context)
        return texts[str(setting)].format(name=self._label(match), value=shown)

    async def _media(self, args: dict[str, Any], area: str | None, cands: list[Candidate],
                     texts: dict[str, Any], context: Context) -> str:
        action = args.get("action")
        services = {
            "play": ("media_play", {}), "pause": ("media_pause", {}),
            "next": ("media_next_track", {}), "previous": ("media_previous_track", {}),
            "mute": ("volume_mute", {"is_volume_muted": True}),
            "unmute": ("volume_mute", {"is_volume_muted": False}),
        }
        if action not in services:
            raise Unresolved(f"media action {action!r}")
        players = [c for c in cands if c.domain == "media_player"]
        if args.get("device"):
            match = self._resolve(args.get("device"), area, cands, {"media_player"})
        elif area and (in_area := [c for c in players if c.area == area]):
            match = MatchResult(in_area, 1.0, group=True)
        else:
            active = [c for c in players
                      if (s := self.hass.states.get(c.entity_id)) and s.state in ("playing", "paused")]
            if len(active) != 1:
                raise Unresolved("no unique media player")
            match = MatchResult(active, 1.0)
        service, data = services[action]
        for ent in match.entities:
            await self._call("media_player", service, ent.entity_id, data, context)
        return texts[action].format(name=self._label(match))

    def _get_state(self, args: dict[str, Any], area: str | None, cands: list[Candidate],
                   texts: dict[str, Any]) -> str:
        measure = args.get("measure") or "state"
        device = args.get("device")
        if measure == "state":
            return self._describe_state(self._resolve(device, area, cands, None), texts)
        if measure not in MEASURE_CLASSES:
            raise Unresolved(f"measure {measure!r}")
        pool = [
            c for c in cands
            if (c.domain == "sensor" and c.device_class == MEASURE_CLASSES[measure])
            or (c.domain == "climate" and measure in ("temperature", "humidity"))
        ]
        match = None
        if device:
            match = resolve(str(device), area, pool, threshold=MATCH_THRESHOLD)
        if match is None:
            group = [c for c in pool if area and c.area == area]
            # Prefer real sensors over a thermostat's own reading.
            sensors = [c for c in group if c.domain == "sensor"] or group
            if not sensors:
                raise Unresolved(f"no {measure} in {area!r}")
            match = MatchResult(sensors, 1.0, group=True)
        parts = []
        for ent in match.entities:
            state = self.hass.states.get(ent.entity_id)
            if state is None:
                continue
            if ent.domain == "climate":
                attr = "current_temperature" if measure == "temperature" else "current_humidity"
                value = state.attributes.get(attr)
                unit = "°C" if measure == "temperature" else "%"
            else:
                value, unit = state.state, state.attributes.get("unit_of_measurement", "")
            if value in (None, "unknown", "unavailable"):
                continue
            parts.append(texts["state"].format(name=ent.names[0], state=f"{value} {unit}".strip()))
        if not parts:
            raise Unresolved("no readable value")
        return " ".join(parts)

    def _describe_state(self, match: MatchResult, texts: dict[str, Any]) -> str:
        if match.group and len(match.entities) > 1:
            on = [c.names[0] for c in match.entities
                  if (s := self.hass.states.get(c.entity_id)) and s.state in ("on", "open", "playing")]
            return texts["group_state_on"].format(names=", ".join(on)) if on else texts["group_state_none"]
        ent = match.entities[0]
        state = self.hass.states.get(ent.entity_id)
        if state is None:
            raise Unresolved("state vanished")
        return texts["state"].format(name=ent.names[0], state=_format_state(state, texts))


def _service_for(domain: str, action: str, state: State | None) -> str | None:
    if action == "toggle":
        if domain in ("light", "switch", "input_boolean", "fan", "media_player", "cover", "climate", "humidifier"):
            return "toggle"
        action = "off" if state is not None and state.state not in ("off", "docked", "idle") else "on"
    table: dict[str, dict[str, str]] = {
        "cover": {"on": "open_cover", "open": "open_cover", "off": "close_cover",
                  "close": "close_cover", "stop": "stop_cover"},
        "vacuum": {"on": "start", "off": "return_to_base", "stop": "stop"},
        "media_player": {"on": "turn_on", "off": "turn_off", "stop": "media_stop"},
    }
    if domain in table:
        return table[domain].get(action)
    return {"on": "turn_on", "off": "turn_off"}.get(action)


def _format_state(state: State, texts: dict[str, Any]) -> str:
    attrs = state.attributes
    if state.domain == "climate":
        current = attrs.get("current_temperature")
        target = attrs.get("temperature")
        parts = [texts["states"].get(state.state, state.state)]
        if current is not None:
            parts.append(f"{current} °")
        if target is not None:
            parts.append(f"→ {target} °")
        return " ".join(parts)
    if state.domain == "weather":
        temp = attrs.get("temperature")
        unit = attrs.get("temperature_unit", "")
        return f"{state.state}, {temp} {unit}".strip(", ")
    unit = attrs.get("unit_of_measurement")
    value = texts["states"].get(state.state, state.state)
    if unit:
        return f"{value} {unit}"
    if state.domain == "light" and state.state == "on" and attrs.get("brightness"):
        return f"{value} ({round(int(attrs['brightness']) / 255 * 100)} %)"
    return value


# ---------------------------------------------------------------------- Whistle
def _engine() -> Any:
    """The Cactus runtime: `cactus` (cactus-compute 4.x), else legacy `needle`."""
    try:
        import cactus
    except ImportError:
        import needle as cactus  # cactus-needle 3.x, engine downloads were withdrawn
    return cactus


def _warm_whistle() -> None:
    """Fetch the Whistle weights and engine once (runs in the executor)."""
    _engine().transcribe(array.array("f", [0.0] * 1600))


def pcm16_to_float(audio: bytes, channels: int = 1) -> array.array:
    """Convert little-endian 16-bit PCM (optionally a WAV file) to mono floats."""
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        # Skip to the data chunk of a WAV container.
        pos = 12
        while pos + 8 <= len(audio):
            chunk_id = audio[pos:pos + 4]
            size = int.from_bytes(audio[pos + 4:pos + 8], "little")
            if chunk_id == b"data":
                audio = audio[pos + 8:pos + 8 + size]
                break
            pos += 8 + size + (size & 1)
    usable = len(audio) - (len(audio) % (2 * channels))
    samples = array.array("h")
    samples.frombytes(audio[:usable])
    if channels > 1:
        samples = array.array("h", samples[::channels])
    return array.array("f", (s / 32768.0 for s in samples))


async def async_transcribe(manager: WhistleAssistManager, audio: bytes, language: str | None,
                           channels: int = 1) -> str:
    """Transcribe 16 kHz PCM without blocking the event loop."""
    samples = pcm16_to_float(audio, channels)
    keywords = manager.keywords()

    def run() -> Any:
        return _engine().transcribe(samples, language=language, keywords=keywords or None)

    async with manager.engine_lock:
        result = await manager.hass.async_add_executor_job(run)
    text = result.get("text", "") if isinstance(result, dict) else ""
    return str(text).strip()
