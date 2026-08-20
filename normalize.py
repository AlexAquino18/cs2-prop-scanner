"""
Normalization helpers so the same real-world bet — same player, same
stat, same map range — collapses to the same key regardless of how
each book labels it.
"""
import re
import unicodedata

# Canonical stat buckets. Keys are the canonical name; values are
# substrings (lowercase) that, if found in a book's stat label, map to
# that canonical stat. Order matters — more specific patterns first.
STAT_ALIASES = [
    ("kills_assists", ["kills+assists", "kills & assists", "kills and assists", "kda"]),
    ("awp_kills", ["awp kill"]),
    ("first_bloods", ["first blood", "firstblood"]),
    ("first_kills", ["first kill", "opening kill", "entry kill"]),
    ("headshots", ["headshot"]),
    ("assists", ["assist"]),
    ("deaths", ["death"]),
    ("adr", ["average damage", "adr"]),
    ("rounds_won", ["rounds won", "round wins"]),
    ("maps_won", ["maps won", "map wins", "series score"]),
    ("clutches", ["clutch"]),
    ("kills", ["kill"]),  # keep last: "kill" matches inside kills+assists etc.
]

MAP_RANGE_PATTERNS = [
    # "Maps 1-2", "Maps 1+2", "Map 1-3", "Map1-2"
    re.compile(r"\bmaps?\s*(\d)\s*[+\-]\s*(\d)\b", re.I),
    # "Map 1", "Map1"
    re.compile(r"\bmap\s*(\d)\b", re.I),
    # "M1-2" / "M1+2" shorthand some books use
    re.compile(r"\bm(\d)\s*[+\-]\s*(\d)\b", re.I),
    re.compile(r"\bm(\d)\b", re.I),
]

SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def normalize_player_name(raw: str) -> str:
    """
    CS2 books almost always show the player's handle (e.g. "s1mple",
    "ZywOo"), not a full legal name, so this is mostly case/punctuation/
    accent folding rather than the "John Smith Jr." logic you'd need
    for traditional sports.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[.\-_'\"]", "", text)
    text = re.sub(r"\s+", " ", text)
    tokens = [t for t in text.split(" ") if t not in SUFFIXES]
    return " ".join(tokens)


def extract_map_range(stat_label: str) -> tuple:
    """
    Returns (cleaned_label, map_range_str_or_None).
    map_range_str is e.g. "1", "1-2", "1-3".
    """
    label = stat_label or ""
    for pattern in MAP_RANGE_PATTERNS:
        m = pattern.search(label)
        if m:
            groups = [g for g in m.groups() if g]
            map_range = "-".join(groups)
            cleaned = pattern.sub("", label).strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            return cleaned, map_range
    return label, None


def normalize_stat(stat_label: str) -> tuple:
    """
    Returns (canonical_stat_key, map_range_or_None).
    Falls back to a slugified version of the original label if nothing
    in STAT_ALIASES matches, so unmapped/new stat types still show up
    in output instead of being silently dropped.
    """
    cleaned, map_range = extract_map_range(stat_label or "")
    lowered = cleaned.lower()
    for canonical, aliases in STAT_ALIASES:
        if any(alias in lowered for alias in aliases):
            return canonical, map_range
    fallback = re.sub(r"[^a-z0-9]+", "_", lowered.strip()).strip("_")
    return fallback or "unknown", map_range
