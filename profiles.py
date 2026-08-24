"""Player / matchup payloads from the local BallDontLie database."""
from __future__ import annotations

from teams import split_matchup
import bdl_sync
import statsdb

MAP_LABELS = {
    "de_ancient": "Ancient",
    "de_anubis": "Anubis",
    "de_dust2": "Dust2",
    "de_inferno": "Inferno",
    "de_mirage": "Mirage",
    "de_nuke": "Nuke",
    "de_overpass": "Overpass",
    "de_train": "Train",
    "de_vertigo": "Vertigo",
    "de_cache": "Cache",
}


def map_label(name: str | None) -> str:
    if not name:
        return "—"
    return MAP_LABELS.get(name, name.replace("de_", "").replace("_", " ").title())


def _opp(row: dict, player_team_id: int | None) -> str:
    t1, t2 = row.get("team1_name") or "", row.get("team2_name") or ""
    if player_team_id and row.get("team1_id") == player_team_id:
        return t2
    if player_team_id and row.get("team2_id") == player_team_id:
        return t1
    return t2 or t1


def _stat_value(row: dict, stat_key: str) -> float | None:
    kills = row.get("kills")
    hs = row.get("hs_pct")
    if stat_key == "kills":
        return None if kills is None else float(kills)
    if stat_key == "headshots":
        if kills is None or hs is None:
            return None
        return round(float(kills) * float(hs) / 100.0, 1)
    if stat_key == "first_kills":
        fk = row.get("first_kills")
        return None if fk is None else float(fk)
    if stat_key == "adr":
        adr = row.get("adr")
        return None if adr is None else float(adr)
    if stat_key == "assists":
        a = row.get("assists")
        return None if a is None else float(a)
    if stat_key == "deaths":
        d = row.get("deaths")
        return None if d is None else float(d)
    return None if kills is None else float(kills)


def _vs(value: float | None, line: float | None) -> str | None:
    if value is None or line is None:
        return None
    if value > line:
        return "over"
    if value < line:
        return "under"
    return "push"


def _sample(
    row: dict,
    value: float,
    maps: str,
    team_id: int | None,
    line: float | None,
) -> dict:
    return {
        "start": row.get("start_time"),
        "opponent": _opp(row, team_id),
        "maps": maps,
        "value": value,
        "kills": row.get("kills"),
        "deaths": row.get("deaths"),
        "adr": row.get("adr"),
        "hs_pct": row.get("hs_pct"),
        "first_kills": row.get("first_kills"),
        "rating": row.get("rating"),
        "vs": _vs(value, line),
    }


def _map_samples(
    map_rows: list[dict],
    stat_key: str,
    team_id: int | None,
    line: float | None,
    map_number: int | None = None,
    limit: int = 10,
) -> list[dict]:
    out = []
    for row in map_rows:
        if map_number is not None and row.get("map_number") != map_number:
            continue
        val = _stat_value(row, stat_key)
        if val is None:
            continue
        label = map_label(row.get("map_name"))
        num = row.get("map_number")
        maps = f"{label} · M{num}" if num else label
        out.append(_sample(row, val, maps, team_id, line))
        if len(out) >= limit:
            break
    return out


def _series_sums(
    map_rows: list[dict],
    stat_key: str,
    maps_n: int | None,
    team_id: int | None,
    line: float | None = None,
    complete_only: bool = True,
) -> list[dict]:
    by_match = []
    current = None
    for row in map_rows:
        mid = row.get("match_id")
        if current is None or current["match_id"] != mid:
            current = {"match_id": mid, "maps": []}
            by_match.append(current)
        current["maps"].append(row)
    out = []
    for series in by_match:
        maps = sorted(series["maps"], key=lambda m: m.get("map_number") or 0)
        if maps_n:
            maps = [m for m in maps if (m.get("map_number") or 0) <= maps_n]
        if complete_only and maps_n and len(maps) < maps_n:
            continue
        if not maps:
            continue
        vals = [_stat_value(m, stat_key) for m in maps]
        if any(v is None for v in vals):
            continue
        first = maps[0]
        label = ", ".join(map_label(m.get("map_name")) for m in maps)
        row = _sample(first, round(sum(vals), 1), label, team_id, line)
        row["kills"] = sum(m.get("kills") or 0 for m in maps)
        row["deaths"] = sum(m.get("deaths") or 0 for m in maps)
        row["adr"] = round(sum((m.get("adr") or 0) for m in maps) / len(maps), 1)
        row["hs_pct"] = round(sum((m.get("hs_pct") or 0) for m in maps) / len(maps), 1)
        out.append(row)
    return out


def _match_samples(
    matches: list[dict],
    stat_key: str,
    team_id: int | None,
    line: float | None,
    limit: int = 10,
) -> list[dict]:
    out = []
    for row in matches:
        val = _stat_value(row, stat_key)
        if val is None:
            continue
        maps = f"BO{row.get('best_of') or '?'}"
        out.append(_sample(row, val, maps, team_id, line))
        if len(out) >= limit:
            break
    return out


def _stat_title(stat_key: str, token: str) -> str:
    pretty = (stat_key or "stat").replace("_", " ")
    if token == "1":
        return f"{pretty} · map 1"
    if token in {"1-2", "1-3"}:
        return f"{pretty} · maps {token}"
    return f"{pretty} · series"


def _pool_payload(pool: list[dict]) -> list[dict]:
    out = []
    for p in pool[:8]:
        rate = p.get("win_rate") or 0
        if rate <= 1:
            rate = rate * 100
        out.append(
            {
                "map": p["map_name"],
                "label": map_label(p["map_name"]),
                "played": p["matches_played"],
                "wins": p["wins"],
                "losses": p["losses"],
                "win_rate": round(rate),
            }
        )
    return out


def player_profile(name: str, stat_key: str = "kills", map_range: str = "1-2", line: float | None = None) -> dict:
    statsdb.init_db()
    player, err, queued = bdl_sync.lookup_or_fetch_player(name)
    if not player:
        return {
            "ok": False,
            "queued": queued,
            "status": "loading" if queued else "missing",
            "message": err or "Player not found.",
            "player": name,
            "stat": _stat_title(stat_key, map_range or "full"),
            "line": line,
            "recent": [],
            "maps": [],
        }
    maps = statsdb.player_map_rows(player["id"], limit=15)
    matches = statsdb.player_match_rows(player["id"], limit=10)
    token = map_range or "full"
    note = None
    grain = "map"
    if token == "1":
        samples = _map_samples(maps, stat_key, player.get("team_id"), line, map_number=1)
        if not samples:
            samples = _map_samples(maps, stat_key, player.get("team_id"), line)
            if samples:
                note = "Map 1 splits are still loading — showing recent maps."
    elif token in {"1-2", "1-3"}:
        n = 2 if token == "1-2" else 3
        samples = _series_sums(maps, stat_key, n, player.get("team_id"), line, complete_only=True)
        grain = "series"
        if not samples:
            samples = _map_samples(maps, stat_key, player.get("team_id"), line)
            grain = "map"
            if samples:
                note = "Full series totals are still loading — showing individual maps."
        if not samples:
            samples = _match_samples(matches, stat_key, player.get("team_id"), line)
            grain = "series"
            if samples:
                note = "Map splits are still loading — showing series totals."
    else:
        samples = _match_samples(matches, stat_key, player.get("team_id"), line)
        grain = "series"
        if not samples:
            samples = _map_samples(maps, stat_key, player.get("team_id"), line)
            grain = "map"
    hits = None
    if line is not None and samples:
        overs = sum(1 for s in samples if s["vs"] == "over")
        hits = {
            "line": line,
            "overs": overs,
            "n": len(samples),
            "pct": round(100 * overs / len(samples)),
        }
    avg = round(sum(s["value"] for s in samples) / len(samples), 1) if samples else None
    rank = statsdb.team_rank(player.get("team_id"))
    pool = statsdb.map_pool(player["team_id"]) if player.get("team_id") else []
    cached_at = None
    if player.get("team_id"):
        cached_at = statsdb.meta_get(f"ready_at:{player['team_id']}") or statsdb.meta_get(
            f"pool_at:{player['team_id']}"
        )
    cached_at = cached_at or statsdb.meta_get("last_daily")
    if not samples:
        note = note or "Not in today's snapshot yet. Last 10 maps and map pools refresh daily."
    return {
        "ok": True,
        "queued": False,
        "status": "ready" if samples else "empty",
        "player": player["nickname"],
        "full_name": player.get("full_name"),
        "team": player.get("team_name"),
        "age": player.get("age"),
        "rank": rank.get("rank") if rank else None,
        "stat": _stat_title(stat_key, token),
        "grain": grain,
        "line": line,
        "avg": avg,
        "hits": hits,
        "maps": _pool_payload(pool),
        "recent": samples,
        "cached_at": cached_at,
        "message": note,
    }


def matchup_profile(label: str) -> dict:
    statsdb.init_db()
    sides = split_matchup(label)
    if len(sides) < 2:
        return {"ok": False, "queued": False, "message": "Need two teams.", "teams": []}
    teams = []
    for name in sides[:2]:
        row, _queued = bdl_sync.lookup_or_fetch_team(name)
        if not row:
            teams.append({"name": name, "rank": None, "maps": [], "queued": False})
            continue
        rank = statsdb.team_rank(row["id"])
        pool = statsdb.map_pool(row["id"])
        teams.append(
            {
                "name": row["name"],
                "rank": rank.get("rank") if rank else None,
                "maps": _pool_payload(pool),
                "queued": False,
            }
        )
    empty = [t["name"] for t in teams if not t["maps"]]
    return {
        "ok": True,
        "label": " vs ".join(t["name"] for t in teams),
        "teams": teams,
        "queued": False,
        "status": "ready" if not empty else "empty",
        "message": "Map pools refresh daily in the background." if empty else None,
    }
