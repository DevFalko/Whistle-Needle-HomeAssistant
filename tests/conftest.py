# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: a fake `needle` module so tests never download models."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest


class FakeNeedle:
    """Stand-in for needle.Needle; replies come from `FakeNeedle.replies`."""

    replies: dict[str, dict[str, Any]] = {}
    instances: list[FakeNeedle] = []

    def __init__(self, tools=None, system=None, stateless=False, **_: Any) -> None:
        self.tools = tools
        self.system = system
        FakeNeedle.instances.append(self)

    def complete(self, text: str, max_new_tokens: int = 512) -> dict[str, Any]:
        return FakeNeedle.replies.get(text, {"function_calls": [], "confidence": 0.95})


@pytest.fixture(autouse=True)
def fake_needle(monkeypatch):
    module = types.ModuleType("needle")
    module.Needle = FakeNeedle
    module.transcribe = lambda audio, language=None, keywords=None: {"text": "Licht im Wohnzimmer an", "language": "de"}
    FakeNeedle.replies = {}
    FakeNeedle.instances = []
    monkeypatch.setitem(sys.modules, "needle", module)
    return FakeNeedle


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
