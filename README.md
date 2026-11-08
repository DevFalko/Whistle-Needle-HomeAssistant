# Whistle + Needle for Home Assistant (Whistle Assist)

[![Hassfest](https://github.com/pindapandatv-wq/Whistle-Needle-HomeAssistant/actions/workflows/validate.yaml/badge.svg)](https://github.com/pindapandatv-wq/Whistle-Needle-HomeAssistant/actions/workflows/validate.yaml)


## Fork status (v0.2.0)

This fork rebuilds the integration after it failed on a real Home Assistant 2026.10 install:

- **`needle_init failed` fixed.** v0.1 put every exposed entity into the tool schemas. On a home with ~180 exposed entities that overflowed Needle 3's 4096-token prompt. Needle now gets **five fixed tools** (`control_device`, `set_level`, `get_state`, `activate_scene`, `control_media`). Only area names go into an enum. Device names are copied from the request and resolved to entities by a fuzzy matcher in the integration (`matcher.py`), which handles German compounds, umlauts, small STT errors, room groups and ambiguity.
- **Options dialog (HTTP 500) fixed.** The options flow no longer assigns `config_entry` itself, which raises on current Home Assistant.
- **Chaining: local first, cloud only when needed.**
  1. Home Assistant's own sentences/intents (exact, instant), optional.
  2. Needle on this machine for device commands and status questions.
  3. Anything Needle does not handle (no tool chosen, unknown device, low confidence) goes to a **fallback conversation agent** you pick in the options, e.g. Anthropic (Claude, optionally with web search), OpenAI or Ollama. Follow-ups keep the fallback agent's own conversation.
- **Whistle STT** now feeds samples directly (no temp WAV files), biases recognition towards your room and device names (`keywords`), and shares one lock with Needle because the engine is not thread-safe.
- **Model preloading** at startup, so the first command does not wait for the download.
- **Runtime moved to `cactus-compute` 4.1.0.** Cactus withdrew the engine downloads that `cactus-needle` 3.x relies on; 4.1.0 also ships engines for musl (Home Assistant OS) on x86_64 and aarch64 (Raspberry Pi).
- **Sentence-aware corrections.** Tested against the real model with German commands: Needle sometimes drops or shortens the room, answers "on" for "Rollo zu", or picks a switching tool for a question. The integration therefore takes the room, the direction (an/aus/auf/zu) and the measured quantity (temperature, humidity, power, battery) from the sentence when needed, and **never runs a switching tool for a question** ("Ist das Licht an?").
- **Confidence threshold default 0.10.** Needle's confidence on correct German device commands was 0.11–0.73 (mostly 0.15–0.30), on off-topic requests 0.01–0.10 with no tool chosen. Tune it in the options.
- **Tests** against a real Home Assistant core (`pytest-homeassistant-custom-component`), see [Development](#development-and-checks).

## Intro

An experimental Home Assistant custom integration connecting Cactus Compute's local Whistle speech recognition and Needle tool-calling model to Home Assistant Assist.

- **Whistle speech-to-text entity:** `Whistle`, selectable in an Assist pipeline (en, de, fr, es, it, nl, pl).
- **Conversation agent:** `Whistle Assist`, which runs the chain above.
- **Entity-aware:** only entities exposed to Home Assistant's `conversation` assistant are considered.
- **Constrained control:** a fixed allowlist of actions per domain, values are range-checked before any service call. There is no arbitrary service-call tool.
- **Privacy default:** Cactus telemetry is opted out by default with `CACTUS_TELEMETRY=0` and `DO_NOT_TRACK=1` unless one of these variables is explicitly configured. See [Telemetry](#telemetry). Requests only leave the house when they reach the fallback agent you configured.

> **Experimental software.** Back up Home Assistant and test on a non-critical installation before allowing voice-driven device control. Do not expose security-relevant switches (locks, alarm, DNS filters, ...) to Assist.

## Features

| Tool | What it does |
| --- | --- |
| `control_device` | On/off/toggle lights, switches, fans, heating, media players, humidifiers; open/close/stop covers; start/dock vacuums. "Licht im Wohnzimmer" uses a room-named light group if one exists, otherwise all lights in the room. |
| `set_level` | Brightness %, target temperature (checked against `min_temp`/`max_temp`), cover position %, volume %, fan speed % |
| `get_state` | State of a device or sensor, with unit; for a group, which members are on |
| `activate_scene` | Activate a scene by name |
| `control_media` | Play, pause, next, previous, mute, unmute |

Scripts, locks, alarm panels and buttons are never actuated by Needle. Requests like that go to Home Assistant's intents or to the fallback agent.

## Installation

### HACS custom repository

This repository is not automatically included in the HACS default catalog. Once published to GitHub:

1. Open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/pindapandatv-wq/Whistle-Needle-HomeAssistant` and choose **Integration**.
3. Install **Whistle Assist**, then restart Home Assistant.

### Manual installation

1. Back up your Home Assistant configuration.
2. Copy `custom_components/whistle_assist/` from this repository into `<config>/custom_components/whistle_assist/`.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, and add **Whistle Assist**.
5. Set the language, the confidence threshold and (optionally) the fallback agent. The fallback agent has to be set up first as its own integration (e.g. Anthropic or OpenAI Conversation).
6. Go to **Settings → Voice assistants** and select `Whistle` as the speech-to-text engine and `Whistle Assist` as the conversation agent. Choose a text-to-speech engine separately if you want spoken output.
7. In Home Assistant's voice-assistant entity exposure settings, expose only the entities that you intend Whistle Assist to read/control.

The Home Assistant manifest pins `cactus-compute==4.1.0`. (`cactus-needle` 3.x can no longer download its engine: Cactus moved the runtime to `cactus-compute` on 2026-11-05.) Home Assistant should install that Python dependency during setup; it is not bundled into the repository ZIP.

## Models and network access

Model weights are not included in this repository. The upstream `cactus-compute` package fetches and caches the relevant model assets as needed. Allow outbound network access on first use and make sure the Home Assistant host has enough disk space. Later requests should reuse the cached models. Exact paths, cache lifecycle, and architecture support are controlled by the upstream package and can change by version.

### Telemetry

The Cactus runtime enables anonymous usage telemetry by default; `CACTUS_TELEMETRY=0` or `DO_NOT_TRACK=1` opts out. When none of `CACTUS_TELEMETRY`, `NEEDLE_TELEMETRY` and `DO_NOT_TRACK` is set in the Home Assistant process environment, this integration sets `CACTUS_TELEMETRY=0`, `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1` before using the runtime. If either variable is already configured, the integration respects that explicit environment configuration. These are process-wide environment variables, not per-integration settings. Review the upstream documentation before changing them.

No transcript, device state or command is intentionally sent by this adapter to a remote conversation service. The upstream package's own network/model-download/telemetry behavior is separate; see its documentation and license notices.

## Safety and known limitations

- Expose entities deliberately. The agent only considers entities exposed to Home Assistant Assist and re-checks exposure before execution.
- The tool definitions use an explicit action allowlist. Argument values, domains, current options/modes, and numeric ranges are validated before a service call.
- Requests below the confidence threshold, with an unknown device, or with an ambiguous match (the same partial name in several rooms) are never executed locally. They go to the fallback agent, or the agent answers that it cannot handle them.
- Saying only a room ("mach das Büro aus") acts only on an entity named exactly like the room (a light group), never on every switch in that room.
- The fallback agent has its own rules for device control (for cloud LLMs: the Assist API and the exposed entities). What reaches it leaves your network.
- A successful service call means Home Assistant accepted the request; it does not prove the physical device completed the action.
- Needle is optimized for structured tool calling, not general-purpose conversation. The Cactus team says Needle 3 handles German, French, Spanish, Dutch, Italian and Polish besides English, but there is no published multilingual benchmark. Say the room and device name.
- This build advertises 16 kHz mono PCM/WAV input. Whistle's advertised languages are English, German, French, Spanish, Italian, Dutch, and Polish.
- API compatibility depends on the Home Assistant version and the `cactus-compute` runtime. Test the STT and conversation pipeline after upgrades.

**Do not use voice control as the only safety mechanism** for locks, access control, fire/security systems, heaters, or any other hazardous equipment.

## Development and checks

Run the repository checks locally:

```sh
python scripts/validate_repo.py
python -m compileall -q custom_components/whistle_assist
# Home Assistant itself needs Linux; on Windows run the tests in WSL:
bash scripts/run_tests_wsl.sh tests
```

The tests replace the runtime with a fake, so they never download models. To check the real model against your own entities:

```sh
pip install cactus-compute==4.1.0
python scripts/needle_live_check.py exposed.json [sentences.txt]
```

GitHub Actions runs the local contract checks, Home Assistant Hassfest validation, and HACS integration validation on pushes and pull requests. These checks validate repository structure and metadata; they are not a substitute for testing with a live Home Assistant instance and actual devices.

## Attribution and licensing

This integration is an independent community project; it is not an official Home Assistant or Cactus Compute release. It depends on `cactus-compute==4.1.0`; the upstream Python package declares Apache-2.0. The dependency is installed separately by Home Assistant; neither its source nor model weights are vendored in this repository. Model files fetched at runtime are not redistributed or relicensed by this project, and their applicable terms must be checked upstream before you redistribute those files. See [`NOTICE`](NOTICE) for references and the recommended Needle 3 citation, and [`LICENSE`](LICENSE) for this repository's license.

- Whistle overview: <https://cactuscompute.com/blog/whistle>
- Cactus Needle source, documentation, license, and release history: <https://github.com/cactus-compute/needle>
- Exact PyPI dependency release: <https://pypi.org/project/cactus-compute/4.1.0/>
- Home Assistant custom integration manifest: <https://developers.home-assistant.io/docs/creating_integration_manifest>
- HACS publishing requirements: <https://hacs.xyz/docs/publish/start/>

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development checks and change expectations. Please report integration bugs through the repository's GitHub Issues page once published.
