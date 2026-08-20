"""
Underdog Fantasy CS2 adapter.

Fetches the same public pick'em catalog the Underdog website uses.
No API token required.
"""
import re

import requests

import config
from models import PropLine
from normalize import normalize_player_name, normalize_stat

UNDERDOG_URL = "https://api.underdogfantasy.com/beta/v5/over_under_lines"
CS_SPORT_IDS = {"CS", "CS2", "CSGO"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

_VS_SPLIT = re.compile(r"\s+vs\.?\s+", re.I)


def _player_name(player: dict) -> str:
    first = (player.get("first_name") or "").strip()
    last = (player.get("last_name") or "").strip()
    return " ".join(part for part in (first, last) if part)


def _team_lookup(games: list) -> dict:
    """Map team_id -> short name. CS titles are 'Away vs Home'."""
    lookup = {}
    for game in games:
        title_parts = _VS_SPLIT.split(game.get("title") or "", maxsplit=1)
        abbr_parts = _VS_SPLIT.split(game.get("abbreviated_title") or "", maxsplit=1)
        away_id, home_id = game.get("away_team_id"), game.get("home_team_id")
        pairs = []
        if len(title_parts) == 2:
            pairs.append((away_id, title_parts[0].strip(), home_id, title_parts[1].strip()))
        if len(abbr_parts) == 2:
            pairs.append((away_id, abbr_parts[0].strip(), home_id, abbr_parts[1].strip()))
        for away, away_name, home, home_name in pairs:
            if away and away_name:
                lookup[away] = away_name
            if home and home_name:
                lookup[home] = home_name
    return lookup


def _odds_for(options: list, choice: str):
    for option in options or []:
        if option.get("choice") == choice and option.get("american_price") not in (None, ""):
            return option.get("american_price")
    return None


def fetch(session: requests.Session = None, dump_raw: bool = False) -> list:
    session = session or requests.Session()
    resp = session.get(
        UNDERDOG_URL,
        headers=HEADERS,
        timeout=config.UNDERDOG_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    payload = resp.json()

    players = {p["id"]: p for p in payload.get("players") or [] if p.get("id")}
    appearances = {a["id"]: a for a in payload.get("appearances") or [] if a.get("id")}
    games = {g["id"]: g for g in payload.get("games") or [] if g.get("id") is not None}
    team_names = _team_lookup(payload.get("games") or [])
    cs_player_ids = {
        pid for pid, player in players.items() if player.get("sport_id") in CS_SPORT_IDS
    }

    props = []
    raw_items = []
    for line in payload.get("over_under_lines") or []:
        if line.get("status") not in (None, "active"):
            continue
        if line.get("live_event"):
            continue
        if (line.get("line_type") or "").lower() == "alternate":
            continue

        appearance_stat = (line.get("over_under") or {}).get("appearance_stat") or {}
        appearance = appearances.get(appearance_stat.get("appearance_id"))
        if not appearance:
            continue
        player = players.get(appearance.get("player_id"))
        if not player or player.get("id") not in cs_player_ids:
            continue
        if appearance.get("type") not in (None, "Player"):
            continue

        player_name = _player_name(player)
        stat_label = appearance_stat.get("display_stat") or appearance_stat.get("stat") or ""
        line_value = line.get("stat_value")
        if not player_name or not stat_label or line_value is None:
            continue

        game = games.get(appearance.get("match_id"))
        team = team_names.get(player.get("team_id"))
        opponent = None
        starts_at = None
        if game:
            starts_at = game.get("scheduled_at")
            opponent = game.get("abbreviated_title") or game.get("title")

        item = {
            "player_name": player_name,
            "team": team,
            "stat": stat_label,
            "line": line_value,
            "overOdds": _odds_for(line.get("options"), "higher"),
            "underOdds": _odds_for(line.get("options"), "lower"),
            "opponent": opponent,
            "startTime": starts_at,
            "league": player.get("sport_id"),
            "line_type": line.get("line_type"),
        }
        if dump_raw:
            raw_items.append(item)
            continue

        stat_key, map_range = normalize_stat(str(stat_label))
        props.append(
            PropLine(
                source="underdog",
                player_raw=player_name,
                player_key=normalize_player_name(player_name),
                team=team,
                stat_raw=str(stat_label),
                stat_key=stat_key,
                map_range=map_range,
                line=float(line_value),
                over_odds=item["overOdds"],
                under_odds=item["underOdds"],
                opponent=opponent,
                starts_at=starts_at,
                extra={"raw_league": player.get("sport_id"), "line_type": line.get("line_type")},
            )
        )

    return raw_items if dump_raw else props
