"""
Shared data model every source adapter normalizes into.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PropLine:
    source: str                    # "prizepicks" | "underdog" | "chalkboard"
    player_raw: str                # name exactly as the book shows it
    player_key: str                # normalized name used for matching
    team: Optional[str]            # team/org abbreviation or name, if given
    stat_raw: str                  # stat label exactly as the book shows it
    stat_key: str                  # canonical stat, e.g. "kills", "headshots"
    map_range: Optional[str]       # e.g. "1", "1-2", "1-3", None = full match
    line: float                    # the numeric line (over/under threshold)
    over_odds: Optional[str] = None
    under_odds: Optional[str] = None
    multiplier: Optional[str] = None   # e.g. Underdog "Standard"/"Boosted", PrizePicks "Goblin"/"Demon"
    opponent: Optional[str] = None
    starts_at: Optional[str] = None
    extra: dict = field(default_factory=dict)  # anything source-specific worth keeping

    def match_key(self) -> tuple:
        """Key used to group the same bet across books."""
        return (self.player_key, self.stat_key, self.map_range or "full")


@dataclass
class Discrepancy:
    player: str
    team: Optional[str]
    stat: str
    map_range: Optional[str]
    lines: dict            # {source: PropLine}
    spread: float           # max(line) - min(line)

    def as_row(self):
        low_src = min(self.lines, key=lambda s: self.lines[s].line)
        high_src = max(self.lines, key=lambda s: self.lines[s].line)
        return {
            "player": self.player,
            "team": self.team or "",
            "stat": self.stat,
            "map_range": self.map_range or "full",
            "spread": round(self.spread, 2),
            "low_book": low_src,
            "low_line": self.lines[low_src].line,
            "high_book": high_src,
            "high_line": self.lines[high_src].line,
            "all_lines": {s: p.line for s, p in self.lines.items()},
        }
