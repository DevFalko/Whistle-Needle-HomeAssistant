# SPDX-License-Identifier: Apache-2.0
"""Run the real Needle model on sample sentences and resolve the results.

Usage (needs `pip install cactus-compute`, downloads engine and model once):
    python scripts/needle_live_check.py exposed.json [sentences.txt]

exposed.json maps entity_id -> {"attributes": {"friendly_name": ...}, "area": ...}.
Prints, per sentence, the tool call, confidence, latency and resolved entities.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "whistle_assist"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DEFAULT_SENTENCES = [
    "Mach das Licht im Wohnzimmer an",
    "Schalte die Stehlampe im Büro aus",
    "Dimme die Küche auf 30 Prozent",
    "Stell die Heizung im Wohnzimmer auf 21 Grad",
    "Ist das Licht im Flur an?",
    "Wie warm ist es im Schlafzimmer?",
    "Aktiviere die Szene Galaxie",
    "Nachtlicht in der Küche",
    "Pause die Musik",
    "Mach die Steckdose auf der Terrasse aus",
    "Suche im Internet nach den aktuellen Nachrichten",
    "Wie wird das Wetter morgen?",
    "Erzähl mir einen Witz",
    "Stell einen Timer auf fünf Minuten",
    "Turn off the kitchen lights",
    "Wie hoch ist die Luftfeuchtigkeit im Bad?",
    "Mach das Rollo im Schlafzimmer zu",
    "Schalte alle Lichter im Flur oben aus",
    "Mach das Büro aus",
    "Entspannen im Schlafzimmer",
    "Wohnzimmer Decke auf 50 Prozent",
    "Was läuft gerade im Fernsehen?",
    "Schreib Milch auf die Einkaufsliste",
]


def main() -> int:
    tools_mod = _load("tools")
    matcher = _load("matcher")
    try:
        import cactus as needle
    except ImportError:
        import needle

    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    cands = [
        matcher.Candidate(eid, eid.split(".")[0], [s["attributes"].get("friendly_name") or eid], s.get("area"),
                          s["attributes"].get("device_class"))
        for eid, s in data.items()
    ]
    areas = sorted({c.area for c in cands if c.area})
    sentences = (
        Path(sys.argv[2]).read_text(encoding="utf-8").splitlines() if len(sys.argv) > 2 else DEFAULT_SENTENCES
    )
    tools = tools_mod.build_tools(areas)
    print(f"{len(tools)} tools, {len(json.dumps(tools))} bytes, areas: {areas}")
    t0 = time.perf_counter()
    agent = needle.Needle(tools=tools, system=tools_mod.SYSTEM_PROMPT, stateless=True)
    print(f"init {time.perf_counter() - t0:.2f}s")

    domains_for = {
        "brightness": {"light"}, "temperature": {"climate"}, "position": {"cover"},
        "volume": {"media_player"}, "speed": {"fan"},
    }
    for text in filter(None, (s.strip() for s in sentences)):
        t0 = time.perf_counter()
        response = agent.complete(text)
        ms = (time.perf_counter() - t0) * 1000
        calls = response.get("function_calls") or []
        conf = response.get("confidence")
        print(f"\n> {text}\n  {ms:.0f} ms, confidence {conf}")
        if not calls:
            print("  -> no tool (would go to fallback agent)")
        for call in calls:
            args = call.get("arguments") or {}
            name = call.get("name")
            area = matcher.best_area(args.get("area"), text, areas)
            domains = None
            if name != "get_state" and matcher.is_question(text):
                print(f"  {name} {json.dumps(args, ensure_ascii=False)}\n    -> blocked: question (fallback)")
                continue
            measure = matcher.infer_measure(text) if name == "get_state" else None
            if measure:
                print(f"  {name} {json.dumps(args, ensure_ascii=False)}\n    -> {measure} in {area}")
                continue
            if name == "control_device":
                stated = matcher.infer_action(text)
                if stated and stated != args.get("action"):
                    args = {**args, "action": stated}
                if args.get("action") in ("open", "close"):
                    domains = {"cover"}
            if name == "activate_scene":
                device, domains = args.get("scene"), {"scene"}
            elif name == "set_level":
                device, domains = args.get("device"), domains_for.get(args.get("setting"))
            else:
                device = args.get("device")
            res = matcher.resolve(str(device or ""), area, cands, domains=domains) if device else None
            if res is None and name == "set_level" and area and domains:
                group = [c for c in cands if c.area == area and c.domain in domains]
                res = matcher.MatchResult(group, 1.0, group=True) if group else None
            target = [c.entity_id for c in res.entities] if res else None
            print(f"  {name} {json.dumps(args, ensure_ascii=False)}\n    -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
