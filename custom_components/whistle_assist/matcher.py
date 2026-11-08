# SPDX-License-Identifier: Apache-2.0
"""Resolve spoken device and area names to Home Assistant entities.

This module is deliberately free of Home Assistant imports so it can be unit
tested on its own. Needle only copies the device words from the request; the
mapping to entity IDs happens here, which keeps the model prompt small no
matter how many entities are exposed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
import unicodedata

_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Filler words dropped before comparing names (articles, prepositions, ...).
_STOPWORDS = {
    # de
    "der", "die", "das", "den", "dem", "des", "im", "in", "am", "an", "auf",
    "vom", "von", "zum", "zur", "bitte", "mal", "mein", "meine", "meinen",
    "ein", "eine", "einen", "alle", "allen", "ganze", "ganzen",
    # en
    "the", "a", "an", "in", "on", "of", "my", "please", "all",
}

# Words that name a whole device class rather than one device.
DOMAIN_WORDS: dict[str, set[str]] = {
    "light": {
        "licht", "lichter", "lampe", "lampen", "beleuchtung", "leuchte", "leuchten",
        "deckenlicht", "light", "lights", "lamp", "lamps", "lighting",
    },
    "cover": {
        "rollo", "rollos", "rollladen", "rolllaeden", "rolladen", "jalousie",
        "jalousien", "markise", "vorhang", "vorhaenge", "blind", "blinds",
        "shutter", "shutters", "cover", "covers", "curtain", "curtains",
    },
    "climate": {
        "heizung", "heizungen", "thermostat", "thermostate", "klima", "klimaanlage",
        "heating", "heater", "thermostats", "climate", "ac",
    },
    "fan": {"ventilator", "ventilatoren", "luefter", "fan", "fans"},
    "switch": {"steckdose", "steckdosen", "schalter", "plug", "plugs", "socket", "switch", "switches"},
    "media_player": {
        "fernseher", "tv", "musik", "lautsprecher", "radio", "speaker", "speakers",
        "music", "television", "player",
    },
    "vacuum": {"staubsauger", "saugroboter", "sauger", "roboter", "vacuum", "robot"},
}


def _similar(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.84)


def normalize(text: str) -> str:
    """Lower-case, fold umlauts/accents and strip punctuation."""
    text = text.casefold().translate(_FOLD)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def tokens(text: str) -> list[str]:
    return [tok for tok in normalize(text).split() if tok not in _STOPWORDS]


@dataclass
class Candidate:
    """One exposed entity as seen by the matcher."""

    entity_id: str
    domain: str
    names: list[str]
    area: str | None = None
    _token_sets: list[set[str]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._token_sets = [set(tokens(name)) for name in self.names if name]


def _score_name(query: str, query_tokens: set[str], name: str, name_tokens: set[str],
                area_tokens: set[str]) -> float:
    """Score how well a spoken phrase matches one entity name (0..1)."""
    if not name_tokens or not query_tokens:
        return 0.0
    # Entity names often start with the area ("Wohnzimmer Decke"); the area is
    # judged separately, so compare without it when that leaves something.
    stripped = name_tokens - area_tokens or name_tokens
    q = query_tokens - area_tokens or query_tokens

    if query_tokens == name_tokens:
        return 1.0
    best = 0.0
    for candidate in (name_tokens, stripped):
        if q == candidate:
            best = max(best, 0.97)
            continue
        overlap = len(q & candidate)
        if overlap:
            # Share of the spoken words found in the name, slightly penalising
            # names that carry many extra words.
            recall = overlap / len(q)
            precision = overlap / len(candidate)
            best = max(best, 0.75 * recall + 0.25 * precision)
    # Per-token comparison: tolerates small STT errors ("wohnzimer") and German
    # compounds ("Deckenlampe" ~ "Decke"), but not merely similar words
    # ("Nachrichten" vs "Nachtlicht").
    hits = 0.0
    for word in q:
        word_best = 0.0
        for other in stripped:
            ratio = SequenceMatcher(None, word, other).ratio()
            if ratio >= 0.84:
                word_best = max(word_best, ratio)
            elif min(len(word), len(other)) >= 4 and (word.startswith(other) or other.startswith(word)):
                word_best = max(word_best, 0.8)
        hits += word_best
    fuzzy = hits / len(q) * 0.9
    return max(best, fuzzy)


@dataclass
class MatchResult:
    entities: list[Candidate]
    score: float
    group: bool = False  # True when a device-class word matched a whole area


def domain_for_word(phrase: str) -> str | None:
    """Return the domain a phrase names generically ("Licht", "Rollos"), if any."""
    toks = tokens(phrase)
    if not toks:
        return None
    for domain, words in DOMAIN_WORDS.items():
        if all(tok in words for tok in toks):
            return domain
    return None


def match_area(spoken: str | None, areas: list[str]) -> str | None:
    """Map a spoken area to one of the known area names."""
    if not spoken:
        return None
    norm = normalize(spoken)
    if not norm:
        return None
    best, best_score = None, 0.0
    for area in areas:
        score = SequenceMatcher(None, norm, normalize(area)).ratio()
        if normalize(area) == norm:
            return area
        if score > best_score:
            best, best_score = area, score
    return best if best_score >= 0.75 else None


def resolve(
    device: str | None,
    area: str | None,
    candidates: list[Candidate],
    *,
    domains: set[str] | None = None,
    threshold: float = 0.62,
) -> MatchResult | None:
    """Resolve a spoken device (and optional area) to one or more entities."""
    pool = [c for c in candidates if domains is None or c.domain in domains]
    if not area and device:
        # The room may be part of the device phrase ("die Stehlampe im Büro"),
        # possibly misheard ("Wohnzimer").
        spoken = tokens(device)
        best_area, used = None, []
        for name in {c.area for c in pool if c.area}:
            area_toks = tokens(name)
            hits = [w for w in spoken if any(_similar(w, t) for t in area_toks)]
            if area_toks and len(hits) >= len(area_toks) and len(area_toks) > len(tokens(best_area or "")):
                best_area, used = name, hits
        if best_area:
            area = best_area
            rest = [w for w in spoken if w not in used]
            # Keep the canonical room words so names like "Flur oben" still compare.
            device = " ".join(rest + tokens(best_area))
    if area:
        in_area = [c for c in pool if c.area and normalize(c.area) == normalize(area)]
    else:
        in_area = []

    device = (device or "").strip()
    generic = domain_for_word(device) if device else None

    # "Licht im Wohnzimmer" -> every exposed light in that area.
    if generic and area:
        group = [c for c in in_area if c.domain == generic]
        # A light group named like the room ("Wohnzimmer") already covers it.
        room_group = [c for c in group if any(normalize(n) == normalize(area) for n in c.names)]
        if room_group:
            return MatchResult(room_group[:1], 1.0, group=True)
        if group:
            return MatchResult(group, 1.0, group=True)

    if not device:
        return None

    query_tokens = set(tokens(device))
    area_tokens: set[str] = set()
    for c in pool:
        if c.area:
            area_tokens |= set(tokens(c.area))

    # Only the room was said ("mach das Wohnzimmer an"): accept nothing but an
    # entity named exactly like the room, e.g. a light group. Never fan out to
    # every switch in a room.
    if area and query_tokens <= set(tokens(area)):
        room = [c for c in in_area if any(normalize(n) == normalize(area) for n in c.names)]
        return MatchResult(room[:1], 1.0, group=True) if room else None

    def best_of(items: list[Candidate]) -> tuple[list[Candidate], float]:
        scored: list[tuple[float, Candidate]] = []
        for cand in items:
            score = 0.0
            for name, name_tokens in zip([n for n in cand.names if n], cand._token_sets, strict=False):
                score = max(score, _score_name(device, query_tokens, name, name_tokens, area_tokens))
            if generic and cand.domain == generic:
                score += 0.05
            scored.append((score, cand))
        if not scored:
            return [], 0.0
        top = max(s for s, _ in scored)
        tied = [c for s, c in scored if top - s < 0.01]
        # Prefer the tightest names ("Echo Dot 2" over "Echo Dot 2 Media Player");
        # equally tight matches ("Decke" -> Decke 1..4) are acted on together.
        if top < 0.97 and len({c.area for c in tied}) > 1:
            # Partial match in several rooms ("Energie tanken im Bad" with an
            # unknown room word): too risky to guess.
            return [], top
        shortest = min(min(len(t) for t in c._token_sets) for c in tied)
        return [c for c in tied if min(len(t) for t in c._token_sets) == shortest], top

    if in_area:
        cands, score = best_of(in_area)
        if cands and score >= threshold:
            return MatchResult(cands, min(score, 1.0), group=len(cands) > 1)
    cands, score = best_of(pool)
    if cands and score >= threshold:
        # Entity names often contain the room ("Küche Spots"); when the user
        # named an area that the entity is not in, require a stronger match.
        if area and in_area and score < 0.9:
            return None
        # A bare class word ("Licht") without a room must match a name exactly.
        if generic and not area and score < 0.97:
            cands = []
        elif len(cands) > 1 and not (area and in_area):
            # Ties across rooms are ambiguous.
            return None
        if cands:
            return MatchResult(cands, min(score, 1.0))

    # Only a class word without an area ("mach das Licht an"): ambiguous unless
    # exactly one entity of that class exists.
    if generic:
        group = [c for c in pool if c.domain == generic]
        if len(group) == 1:
            return MatchResult(group, 0.8)
    return None
