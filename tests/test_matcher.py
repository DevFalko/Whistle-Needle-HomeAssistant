# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the entity matcher (no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "whistle_assist" / "matcher.py"
_spec = importlib.util.spec_from_file_location("matcher", _PATH)
matcher = importlib.util.module_from_spec(_spec)
sys.modules["matcher"] = matcher
_spec.loader.exec_module(matcher)

C = matcher.Candidate

HOME = [
    C("light.wohnzimmer", "light", ["Wohnzimmer"], "Wohnzimmer"),
    C("light.wohnzimmer_decke", "light", ["Wohnzimmer Decke"], "Wohnzimmer"),
    C("light.wohnzimmer_stehlampe", "light", ["Wohnzimmer Stehlampe"], "Wohnzimmer"),
    C("light.wohnzimmer_stehlampe_ecke", "light", ["Stehlampe Ecke"], "Wohnzimmer"),
    C("light.buro_stehlampe", "light", ["Büro Stehlampe"], "Büro"),
    C("light.buro_decke", "light", ["Büro Decke"], "Büro"),
    C("light.kuche_decke_1", "light", ["Küche Decke 1"], "Küche"),
    C("light.kuche_decke_2", "light", ["Küche Decke 2"], "Küche"),
    C("light.fernseher", "light", ["Fernseher"], None),
    C("switch.terrasse_steckdose", "switch", ["Terrasse Steckdose"], "Terrasse"),
    C("climate.buro", "climate", ["Büro Thermostat", "Heizung Büro"], "Büro"),
    C("scene.schlafzimmer_entspannen", "scene", ["Schlafzimmer Entspannen"], "Schlafzimmer"),
    C("scene.buro_entspannen", "scene", ["Entspannen"], "Büro"),
    C("scene.flur_nachtlicht", "scene", ["Flur Nachtlicht"], "Flur"),
]
AREAS = sorted({c.area for c in HOME if c.area})


def ids(result):
    return sorted(c.entity_id for c in result.entities) if result else None


@pytest.mark.parametrize(
    ("device", "area", "domains", "expected"),
    [
        ("Licht", "Wohnzimmer", None, ["light.wohnzimmer"]),
        ("Lampen", "Küche", None, ["light.kuche_decke_1", "light.kuche_decke_2"]),
        ("Stehlampe", "Büro", None, ["light.buro_stehlampe"]),
        ("Stehlampe", "Wohnzimmer", None, ["light.wohnzimmer_stehlampe"]),
        ("Büro Stehlampe", None, None, ["light.buro_stehlampe"]),
        ("die Deckenlampe im Büro", None, None, ["light.buro_decke"]),
        ("Decke", "Büro", None, ["light.buro_decke"]),
        ("Steckdose", "Terrasse", None, ["switch.terrasse_steckdose"]),
        ("Heizung", "Büro", {"climate"}, ["climate.buro"]),
        ("wohnzimer decke", None, None, ["light.wohnzimmer_decke"]),
        ("Schlafzimmer Entspannen", None, {"scene"}, ["scene.schlafzimmer_entspannen"]),
        ("Entspannen", None, {"scene"}, ["scene.buro_entspannen"]),
        ("Nachtlicht", None, {"scene"}, ["scene.flur_nachtlicht"]),
        ("Fernseher", None, {"light"}, ["light.fernseher"]),
    ],
)
def test_resolve(device, area, domains, expected):
    assert ids(matcher.resolve(device, area, HOME, domains=domains)) == expected


@pytest.mark.parametrize("device", ["Kaffeemaschine", "Nachrichten", "Garage"])
def test_unknown_devices_do_not_match(device):
    assert matcher.resolve(device, None, HOME) is None


def test_partial_match_in_several_rooms_is_ambiguous():
    home = [
        C("scene.bad_energie", "scene", ["Badezimmer Energie tanken"], "Badezimmer"),
        C("scene.buro_energie", "scene", ["Energie tanken"], "Büro"),
        C("scene.schlaf_energie", "scene", ["Schlafzimmer Energie tanken"], "Schlafzimmer"),
    ]
    assert matcher.resolve("Energie tanken im Bad", None, home, domains={"scene"}) is None
    assert ids(matcher.resolve("Energie tanken im Badezimmer", None, home, domains={"scene"})) == ["scene.bad_energie"]


def test_tie_prefers_tightest_name():
    home = [
        C("media_player.echo", "media_player", ["Echo Dot 2"], None),
        C("media_player.echo_mp", "media_player", ["Echo Dot 2 Media Player"], None),
    ]
    assert ids(matcher.resolve("Echo Dot", None, home)) == ["media_player.echo"]


def test_room_only_phrase_needs_room_named_entity():
    home = HOME + [C("switch.buro_adguard", "switch", ["AdGuard Schutz"], "Büro")]
    assert matcher.resolve("Büro", None, home, domains={"switch"}) is None
    assert ids(matcher.resolve("Wohnzimmer", None, home)) == ["light.wohnzimmer"]


def test_generic_word_without_area_is_ambiguous():
    assert matcher.resolve("Licht", None, HOME) is None


def test_area_mismatch_requires_strong_match():
    # "Decke" exists in Wohnzimmer and Büro; asking for the Küche must pick a
    # Küche ceiling light, never one from another room.
    result = matcher.resolve("Decke", "Küche", HOME)
    assert result is not None
    assert all(c.area == "Küche" for c in result.entities)


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [("Büro", "Büro"), ("buero", "Büro"), ("Kueche", "Küche"), ("Wohnzimmer", "Wohnzimmer"), ("Garten", None)],
)
def test_match_area(spoken, expected):
    assert matcher.match_area(spoken, AREAS) == expected


AREAS_REAL = ["Badezimmer", "Büro", "Flur", "Flur oben", "Küche", "Schlafzimmer", "Wohnzimmer"]


@pytest.mark.parametrize(
    ("model_area", "text", "expected"),
    [
        (None, "Nachtlicht in der Küche", "Küche"),
        ("Flur", "Schalte alle Lichter im Flur oben aus", "Flur oben"),
        ("Büro", "Schalte die Stehlampe im Büro aus", "Büro"),
        (None, "Aktiviere die Szene Galaxie", None),
    ],
)
def test_best_area(model_area, text, expected):
    assert matcher.best_area(model_area, text, AREAS_REAL) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ist das Licht im Flur an?", True),
        ("ist das licht im flur an", True),
        ("Wie warm ist es im Schlafzimmer", True),
        ("Mach das Licht im Wohnzimmer an", False),
        ("Schalte die Stehlampe aus", False),
    ],
)
def test_is_question(text, expected):
    assert matcher.is_question(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mach das Rollo im Schlafzimmer zu", "close"),
        ("Mach das Büro aus", "off"),
        ("Mach das Licht im Wohnzimmer an", "on"),
        ("Mach die Steckdose auf der Terrasse aus", None),  # "auf" + "aus": ambiguous, keep Needle's choice
        ("Toggle the lamp", None),
    ],
)
def test_infer_action(text, expected):
    assert matcher.infer_action(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Wie warm ist es im Schlafzimmer?", "temperature"),
        ("Wie hoch ist die Luftfeuchtigkeit im Bad?", "humidity"),
        ("Wie viel Strom verbraucht die Waschmaschine?", "power"),
        ("Ist das Licht an?", None),
    ],
)
def test_infer_measure(text, expected):
    assert matcher.infer_measure(text) == expected
