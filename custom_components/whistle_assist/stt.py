# SPDX-License-Identifier: Apache-2.0
"""Home Assistant speech-to-text entity backed by Whistle."""

from __future__ import annotations

from collections.abc import AsyncIterable
import logging

from homeassistant.components.stt import (
    AudioBitRates,
    AudioChannels,
    AudioCodecs,
    AudioFormats,
    AudioSampleRates,
    SpeechMetadata,
    SpeechResult,
    SpeechResultState,
    SpeechToTextEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .agent import WhistleAssistManager, async_transcribe
from .const import DOMAIN, SUPPORTED_LANGUAGES

_LOGGER = logging.getLogger(__name__)
# 16 kHz * 2 bytes * 120 s
MAX_AUDIO_BYTES = 16000 * 2 * 120


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up the Whistle STT entity."""
    manager: WhistleAssistManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WhistleSpeechToTextEntity(manager, entry)])


class WhistleSpeechToTextEntity(SpeechToTextEntity):
    """Transcribe 16 kHz mono PCM locally with Whistle."""

    _attr_name = "Whistle"

    def __init__(self, manager: WhistleAssistManager, entry: ConfigEntry) -> None:
        self.manager = manager
        self._attr_unique_id = f"{entry.entry_id}_stt"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def supported_languages(self) -> list[str]:
        return SUPPORTED_LANGUAGES

    @property
    def supported_formats(self) -> list[AudioFormats]:
        return [AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[AudioCodecs]:
        return [AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[AudioBitRates]:
        return [AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[AudioSampleRates]:
        return [AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[AudioChannels]:
        return [AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
        self, metadata: SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> SpeechResult:
        """Collect the audio and run Whistle off the event loop."""
        audio = bytearray()
        try:
            async for chunk in stream:
                audio.extend(chunk)
                if len(audio) > MAX_AUDIO_BYTES:
                    _LOGGER.warning("Whistle input longer than 120 s, truncating")
                    break
            if not audio:
                return SpeechResult(None, SpeechResultState.ERROR)

            language = self.manager.language
            if language == "auto":
                language = (metadata.language or "").lower().split("-", maxsplit=1)[0]
                if language not in SUPPORTED_LANGUAGES:
                    language = None
            transcript = await async_transcribe(
                self.manager, bytes(audio), language, channels=int(metadata.channel)
            )
            _LOGGER.debug("Whistle transcript: %r", transcript)
            # Whistle returns an empty transcript for silence.
            return SpeechResult(transcript or None, SpeechResultState.SUCCESS)
        except Exception:
            _LOGGER.exception("Whistle transcription failed")
            return SpeechResult(None, SpeechResultState.ERROR)
