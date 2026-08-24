"""Normalize CS2 org names so PrizePicks, Underdog, and Polymarket match."""
from __future__ import annotations

import re
import unicodedata

NOISE = {
    "academy",
    "clan",
    "club",
    "esport",
    "esports",
    "gaming",
    "gg",
    "team",
    "the",
}

# Short codes and alternate spellings -> a stable key.
ALIASES = {
    "3dmax": "3dmax",
    "9z": "9z",
    "astralis": "astralis",
    "ast": "astralis",
    "ast10": "astralis",
    "aurora": "aurora",
    "aur": "aurora",
    "auroragaming": "aurora",
    "b8": "b8",
    "b8e": "b8",
    "betboom": "betboom",
    "bb": "betboom",
    "bushidowildcats": "bushido",
    "bw": "bushido",
    "cloud9": "cloud9",
    "c9": "cloud9",
    "complexity": "complexity",
    "ence": "ence",
    "eternalfire": "eternalfire",
    "ef": "eternalfire",
    "faze": "faze",
    "fazeclan": "faze",
    "falcons": "falcons",
    "fal": "falcons",
    "fnatic": "fnatic",
    "fnc": "fnatic",
    "fut": "fut",
    "furia": "furia",
    "g2": "g2",
    "gamerlegion": "gamerlegion",
    "gl": "gamerlegion",
    "gl1": "gamerlegion",
    "heroic": "heroic",
    "imperial": "imperial",
    "lavked": "lavked",
    "lav": "lavked",
    "legacy": "legacy",
    "lgc": "legacy",
    "liquid": "liquid",
    "metizport": "metizport",
    "mzp": "metizport",
    "mibr": "mibr",
    "mongolz": "mongolz",
    "themongolz": "mongolz",
    "mglz": "mongolz",
    "mouz": "mouz",
    "mousesports": "mouz",
    "navi": "navi",
    "natusvincere": "navi",
    "nip": "nip",
    "ninjasinpyjamas": "nip",
    "nrg": "nrg",
    "ntr": "nucleartigeres",
    "nucleartigeres": "nucleartigeres",
    "tsa": "tsa",
    "og": "og",
    "pain": "pain",
    "parivision": "parivision",
    "saw": "saw",
    "sparta": "sparta",
    "spirit": "spirit",
    "teamspirit": "spirit",
    "ts": "spirit",
    "ts7": "spirit",
    "vitality": "vitality",
    "vit": "vitality",
    "virtuspro": "virtuspro",
    "vp": "virtuspro",
    "wildcard": "wildcard",
}


def slug_team(raw: str | None) -> str:
    if not raw:
        return ""
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[.&/'`]", " ", text)
    text = re.sub(r"[-_]", "", text)
    text = re.sub(r"\s+", " ", text)
    tokens = [t for t in text.split(" ") if t and t not in NOISE]
    return "".join(tokens)


def team_key(raw: str | None) -> str:
    slug = slug_team(raw)
    if not slug:
        return ""
    return ALIASES.get(slug, slug)


def split_matchup(text: str | None) -> list[str]:
    if not text:
        return []
    parts = re.split(r"\s+vs\.?\s+", text.strip(), flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def matchup_keys(matchup: str | None, team: str | None = None) -> set[str]:
    keys = {team_key(part) for part in split_matchup(matchup)}
    if team:
        keys.add(team_key(team))
    keys.discard("")
    return keys
