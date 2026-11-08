# SPDX-License-Identifier: Apache-2.0
"""Assist conversation agent: HA intents -> local Needle -> optional fallback agent."""

from __future__ import annotations

import logging
from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from .agent import LocalOutcome, WhistleAssistManager, _text
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up the Whistle Assist conversation entity."""
    manager: WhistleAssistManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WhistleAssistConversationEntity(manager, entry)])


class WhistleAssistConversationEntity(conversation.ConversationEntity):
    """Handle locally what a small model can, hand the rest to a bigger agent."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, manager: WhistleAssistManager, entry: ConfigEntry) -> None:
        self.manager = manager
        self._attr_unique_id = f"{entry.entry_id}_conversation"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Whistle Assist",
            "manufacturer": "Cactus Compute (Needle/Whistle)",
            "entry_type": "service",
        }
        self._attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        # Needle handles en/de/fr/es/it/nl/pl; other languages go to HA intents
        # and the fallback agent, so accept every language.
        return MATCH_ALL

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        language = user_input.language

        # 1. Home Assistant's own sentence triggers and intents: exact and instant.
        if self.manager.use_ha_intents:
            speech = await self._async_try_ha(user_input, chat_log)
            if speech is not None:
                return self._result(user_input, chat_log, speech, stage="ha")

        # 2. Needle on this machine.
        outcome: LocalOutcome = await self.manager.async_interpret(
            user_input.text, language, user_input.context
        )
        _LOGGER.debug("Local stage for %r: %s", user_input.text, outcome)
        if outcome.handled:
            return self._result(user_input, chat_log, outcome.speech, stage="needle")

        # 3. Anything else goes to the configured bigger agent (Claude, OpenAI, ...).
        fallback = self.manager.fallback_agent
        if fallback and fallback != self.entity_id:
            return await self._async_fallback(user_input, chat_log, fallback)

        return self._result(user_input, chat_log, _text(language)["no_local"], stage="none")

    async def _async_try_ha(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> str | None:
        try:
            from homeassistant.components.conversation import (
                async_handle_intents,
                async_handle_sentence_triggers,
            )
        except ImportError:
            return None
        try:
            if (text := await async_handle_sentence_triggers(self.hass, user_input, chat_log)) is not None:
                return text
            response = await async_handle_intents(self.hass, user_input, chat_log)
        except Exception:  # noqa: BLE001 - the local chain must keep going
            _LOGGER.exception("Home Assistant intent matching failed")
            return None
        if response is None or response.response_type == intent.IntentResponseType.ERROR:
            return None
        return response.speech.get("plain", {}).get("speech", "") or "OK"

    async def _async_fallback(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        agent_id: str,
    ) -> conversation.ConversationResult:
        own_id = chat_log.conversation_id
        try:
            result = await conversation.async_converse(
                self.hass,
                user_input.text,
                self.manager.fallback_conversations.get(own_id),
                user_input.context,
                language=user_input.language,
                agent_id=agent_id,
                device_id=user_input.device_id,
                satellite_id=user_input.satellite_id,
                extra_system_prompt=user_input.extra_system_prompt,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Fallback agent %s failed", agent_id)
            return self._result(user_input, chat_log, _text(user_input.language)["no_local"], stage="fallback_error")

        if result.conversation_id:
            self.manager.fallback_conversations[own_id] = result.conversation_id
            # Keep the map bounded.
            while len(self.manager.fallback_conversations) > 200:
                self.manager.fallback_conversations.pop(next(iter(self.manager.fallback_conversations)))
        speech = result.response.speech.get("plain", {}).get("speech", "")
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=speech)
        )
        _LOGGER.debug("Fallback %s answered %r", agent_id, speech)
        return conversation.ConversationResult(
            response=result.response,
            conversation_id=own_id,
            continue_conversation=result.continue_conversation,
        )

    def _result(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        speech: str,
        *,
        stage: str,
    ) -> conversation.ConversationResult:
        _LOGGER.debug("Answered by %s: %r", stage, speech)
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=speech)
        )
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(speech)
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)
