# SPDX-License-Identifier: Apache-2.0
"""Config flow and conversation chain tests against a real Home Assistant core."""

from __future__ import annotations

from typing import Any, Literal

import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.whistle_assist.const import (
    CONF_CONFIDENCE_THRESHOLD,
    CONF_FALLBACK_AGENT,
    CONF_LANGUAGE,
    CONF_PRELOAD,
    CONF_USE_HA_INTENTS,
    DOMAIN,
)

AGENT = "conversation.whistle_assist"


class EchoAgent(conversation.AbstractConversationAgent):
    """A fake cloud agent that records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[conversation.ConversationInput] = []

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        self.calls.append(user_input)
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(f"Cloud: {user_input.text}")
        return conversation.ConversationResult(response=response, conversation_id="cloud-conv-1")


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    data = {
        CONF_LANGUAGE: "de",
        CONF_CONFIDENCE_THRESHOLD: 0.7,
        CONF_USE_HA_INTENTS: False,
        CONF_FALLBACK_AGENT: "",
        CONF_PRELOAD: False,
    } | options
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _light(hass: HomeAssistant, entity_id: str, name: str, area: str | None) -> None:
    ent_reg = er.async_get(hass)
    object_id = entity_id.split(".", 1)[1]
    entry = ent_reg.async_get_or_create(
        "light", "test", object_id, suggested_object_id=object_id, original_name=name
    )
    if area:
        area_entry = ar.async_get(hass).async_get_or_create(area)
        ent_reg.async_update_entity(entry.entity_id, area_id=area_entry.id)
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": name})
    async_expose_entity(hass, "conversation", entry.entity_id, True)


async def _ask(hass: HomeAssistant, text: str, conversation_id: str | None = None) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass, text, conversation_id, Context(), language="de", agent_id=AGENT
    )


# ------------------------------------------------------------------ config flow
async def test_config_flow_creates_entry(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_LANGUAGE: "de", CONF_CONFIDENCE_THRESHOLD: 0.7, CONF_USE_HA_INTENTS: True, CONF_PRELOAD: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_FALLBACK_AGENT] == ""


async def test_options_flow_opens_and_saves(hass: HomeAssistant) -> None:
    """Regression: opening the options used to raise (HTTP 500)."""
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_LANGUAGE: "de", CONF_CONFIDENCE_THRESHOLD: 0.5, CONF_USE_HA_INTENTS: True,
         CONF_FALLBACK_AGENT: "conversation.home_assistant", CONF_PRELOAD: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_FALLBACK_AGENT] == "conversation.home_assistant"


# ------------------------------------------------------------------ toolset size
async def test_toolset_is_small(hass: HomeAssistant, fake_needle) -> None:
    await _setup(hass)
    for i in range(150):
        _light(hass, f"light.lampe_{i}", f"Lampe {i}", "Wohnzimmer")
    fake_needle.replies["mach was"] = {"function_calls": [], "confidence": 0.9}
    await _ask(hass, "mach was")
    tools = fake_needle.instances[-1].tools
    assert len(tools) <= 5
    # Entities must not leak into the prompt.
    assert "light.lampe_1" not in str(tools)


# ------------------------------------------------------------------ local stage
async def test_needle_controls_light_group(hass: HomeAssistant, fake_needle) -> None:
    await _setup(hass)
    _light(hass, "light.wohnzimmer", "Wohnzimmer", "Wohnzimmer")
    _light(hass, "light.wohnzimmer_decke", "Wohnzimmer Decke", "Wohnzimmer")
    calls = async_mock_service(hass, "light", "turn_on")
    fake_needle.replies["Licht im Wohnzimmer an"] = {
        "function_calls": [{"name": "control_device", "arguments": {"action": "on", "device": "Licht", "area": "Wohnzimmer"}}],
        "confidence": 0.93,
    }
    result = await _ask(hass, "Licht im Wohnzimmer an")
    assert [c.data["entity_id"] for c in calls] == ["light.wohnzimmer"]
    assert result.response.speech["plain"]["speech"] == "Wohnzimmer ist an."


async def test_brightness(hass: HomeAssistant, fake_needle) -> None:
    await _setup(hass)
    _light(hass, "light.buro_stehlampe", "Büro Stehlampe", "Büro")
    calls = async_mock_service(hass, "light", "turn_on")
    fake_needle.replies["Stehlampe im Büro auf 40 Prozent"] = {
        "function_calls": [{"name": "set_level", "arguments": {"setting": "brightness", "value": 40, "device": "Stehlampe", "area": "Büro"}}],
        "confidence": 0.9,
    }
    await _ask(hass, "Stehlampe im Büro auf 40 Prozent")
    assert calls[0].data == {"entity_id": "light.buro_stehlampe", "brightness_pct": 40}


# ------------------------------------------------------------------ fallback
@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("Suche im Internet nach den aktuellen Nachrichten", {"function_calls": [], "confidence": 0.9}),
        ("Mach die Kaffeemaschine an", {"function_calls": [{"name": "control_device", "arguments": {"action": "on", "device": "Kaffeemaschine"}}], "confidence": 0.9}),
        ("Licht im Wohnzimmer an", {"function_calls": [{"name": "control_device", "arguments": {"action": "on", "device": "Licht", "area": "Wohnzimmer"}}], "confidence": 0.3}),
    ],
    ids=["no_tool", "unknown_device", "low_confidence"],
)
async def test_falls_back_to_cloud_agent(hass: HomeAssistant, fake_needle, text, reply) -> None:
    await _setup(hass)
    cloud = EchoAgent()
    cloud_entry = MockConfigEntry(domain="cloud_test")
    cloud_entry.add_to_hass(hass)
    conversation.async_set_agent(hass, cloud_entry, cloud)
    hass.config_entries.async_update_entry(
        hass.config_entries.async_entries(DOMAIN)[0], options={CONF_FALLBACK_AGENT: cloud_entry.entry_id}
    )
    await hass.async_block_till_done()
    _light(hass, "light.wohnzimmer", "Wohnzimmer", "Wohnzimmer")
    calls = async_mock_service(hass, "light", "turn_on")
    fake_needle.replies[text] = reply

    result = await _ask(hass, text)
    assert result.response.speech["plain"]["speech"] == f"Cloud: {text}"
    assert len(cloud.calls) == 1
    assert calls == []

    # The follow-up keeps the cloud agent's own conversation id.
    await _ask(hass, text, result.conversation_id)
    assert cloud.calls[1].conversation_id == "cloud-conv-1"


async def test_without_fallback_says_so(hass: HomeAssistant) -> None:
    await _setup(hass)
    result = await _ask(hass, "Wie wird das Wetter?")
    assert result.response.speech["plain"]["speech"] == "Das kann ich lokal nicht erledigen."


async def test_ha_intents_run_first(hass: HomeAssistant, fake_needle) -> None:
    await _setup(hass, **{CONF_USE_HA_INTENTS: True})
    assert await async_setup_component(hass, "intent", {})
    assert await async_setup_component(hass, "light", {})
    _light(hass, "light.wohnzimmer_decke", "Wohnzimmer Decke", "Wohnzimmer")
    calls = async_mock_service(hass, "light", "turn_on")
    await _ask(hass, "schalte Wohnzimmer Decke ein")
    # HA's intent handler passes entity_id as a list.
    assert [c.data["entity_id"] for c in calls] == [["light.wohnzimmer_decke"]]
    assert fake_needle.instances == []  # Needle was not needed
