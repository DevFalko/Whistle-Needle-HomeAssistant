# SPDX-License-Identifier: Apache-2.0
"""Config flow for Whistle Assist."""

from __future__ import annotations

from typing import Any

try:  # Home Assistant 2026.x replaced voluptuous with probatio
    import probatio as vol
except ImportError:  # pragma: no cover - older Home Assistant
    import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    ConversationAgentSelector,
    ConversationAgentSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CONFIDENCE_THRESHOLD,
    CONF_FALLBACK_AGENT,
    CONF_LANGUAGE,
    CONF_PRELOAD,
    CONF_USE_HA_INTENTS,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_LANGUAGE,
    DEFAULT_PRELOAD,
    DEFAULT_USE_HA_INTENTS,
    DOMAIN,
)

LANGUAGE_OPTIONS = {
    "auto": "Automatisch / automatic",
    "de": "Deutsch",
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "it": "Italiano",
    "nl": "Nederlands",
    "pl": "Polski",
}


def _schema(current: dict[str, Any]) -> vol.Schema:
    fallback = current.get(CONF_FALLBACK_AGENT) or None
    fallback_key = vol.Optional(CONF_FALLBACK_AGENT, description={"suggested_value": fallback})
    return vol.Schema(
        {
            vol.Required(CONF_LANGUAGE, default=current.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)): SelectSelector(
                SelectSelectorConfig(
                    options=[SelectOptionDict(value=k, label=v) for k, v in LANGUAGE_OPTIONS.items()],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_CONFIDENCE_THRESHOLD,
                default=current.get(CONF_CONFIDENCE_THRESHOLD, DEFAULT_CONFIDENCE_THRESHOLD),
            ): NumberSelector(
                NumberSelectorConfig(min=0.0, max=1.0, step=0.05, mode=NumberSelectorMode.SLIDER)
            ),
            vol.Required(
                CONF_USE_HA_INTENTS, default=current.get(CONF_USE_HA_INTENTS, DEFAULT_USE_HA_INTENTS)
            ): BooleanSelector(),
            fallback_key: ConversationAgentSelector(ConversationAgentSelectorConfig()),
            vol.Required(CONF_PRELOAD, default=current.get(CONF_PRELOAD, DEFAULT_PRELOAD)): BooleanSelector(),
        }
    )


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    data[CONF_CONFIDENCE_THRESHOLD] = float(data.get(CONF_CONFIDENCE_THRESHOLD, DEFAULT_CONFIDENCE_THRESHOLD))
    data[CONF_FALLBACK_AGENT] = data.get(CONF_FALLBACK_AGENT) or ""
    return data


class WhistleAssistConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up Whistle Assist."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Whistle Assist", data=_clean(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        # Home Assistant injects config_entry itself; setting it here raises.
        return WhistleAssistOptionsFlow()


class WhistleAssistOptionsFlow(config_entries.OptionsFlow):
    """Options for Whistle Assist."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=_clean(user_input))
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
