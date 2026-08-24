"""Player / matchup payloads from the local BallDontLie database."""
from __future__ import annotations

from normalize import normalize_player_name
from teams import team_key, split_matchup
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


def _series_sums(map_rows: list[dict], stat_key: str, maps_n: int | None, team_id: int | None) -> list[dict]:
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
        if not maps:
            continue
        vals = [_stat_value(m, stat_key) for m in maps]
        if any(v is None for v in vals):
            continue
        first = maps[0]
        out.append(
            {
                "start": first.get("start_time"),
                "opponent": _opp(first, team_id),
                "maps": ", ".join(map_label(m.get("map_name")) for m in maps),
                "value": round(sum(vals), 1),
                "kills": sum(m.get("kills") or 0 for m in maps),
                "adr": round(sum((m.get("adr") or 0) for m in maps) / len(maps), 1),
                "hs_pct": round(sum((m.get("hs_pct") or 0) for m in maps) / len(maps), 1),
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
            "message": err or "Player not found.",
            "player": name,
        }
    maps = statsdb.player_map_rows(player["id"], limit=12)
    matches = statsdb.player_match_rows(player["id"], limit=10)
    token = map_range or "full"
    if token == "1":
        samples = []
        for row in maps:
            if row.get("map_number") != 1:
                continue
            val = _stat_value(row, stat_key)
            if val is None:
                continue
            samples.append(
                {
                    "start": row.get("start_time"),
                    "opponent": _opp(row, player.get("team_id")),
                    "maps": map_label(row.get("map_name")),
                    "value": val,
                    "kills": row.get("kills"),
                    "deaths": row.get("deaths"),
                    "adr": row.get("adr"),
                    "hs_pct": row.get("hs_pct"),
                    "first_kills": row.get("first_kills"),
                    "rating": row.get("rating"),
                }
            )
        samples = samples[:10]
        label = f"{stat_key} map 1"
    elif token in {"1-2", "1-3"}:
        n = 2 if token == "1-2" else 3
        samples = _series_sums(maps, stat_key, n, player.get("team_id"))[:10]
        label = f"{stat_key} maps {token}"
    else:
        samples = []
        for row in matches:
            val = _stat_value(row, stat_key)
            if val is None:
                continue
            samples.append(
                {
                    "start": row.get("start_time"),
                    "opponent": _opp(row, player.get("team_id")),
                    "maps": f"BO{row.get('best_of') or '?'}",
                    "value": val,
                    "kills": row.get("kills"),
                    "adr": row.get("adr"),
                    "hs_pct": row.get("hs_pct"),
                }
            )
        label = f"{stat_key} series"
    hits = None
    if line is not None and samples:
        overs = sum(1 for s in samples if s["value"] > line)
        hits = {"line": line, "overs": overs, "n": len(samples), "pct": round(100 * overs / len(samples))}
    avg = round(sum(s["value"] for s in samples) / len(samples), 1) if samples else None
    rank = statsdb.team_rank(player.get("team_id"))
    pool = statsdb.map_pool(player["team_id"]) if player.get("team_id") else []
    if not samples:
        bdl_sync.prioritize_names([player.get("nickname") or name], [player.get("team_name") or ""], priority=10)
    return {
        "ok": True,
        "queued": not samples,
        "player": player["nickname"],
        "full_name": player.get("full_name"),
        "team": player.get("team_name"),
        "age": player.get("age"),
        "rank": rank.get("rank") if rank else None,
        "stat": label,
        "avg": avg,
        "hits": hits,
        "maps": [
            {
                "map": p["map_name"],
                "label": map_label(p["map_name"]),
                "played": p["matches_played"],
                "wins": p["wins"],
                "losses": p["losses"],
                "win_rate": round((p["win_rate"] or 0) * (100 if (p["win_rate"] or 0) <= 1 else 1)),
            }
            for p in pool[:8]
        ],
        "recent": samples,
        "message": None if samples else "Pulling recent maps from BallDontLie. Trial is 5 requests/min, so this can take about a minute.",
    }


def matchup_profile(label: str) -> dict:
    statsdb.init_db()
    sides = split_matchup(label)
    if len(sides) < 2:
        return {"ok": False, "message": "Need two teams."}
    teams = []
    missing = []
    for name in sides[:2]:
        row = statsdb.find_team(name, team_key)
        if not row:
            missing.append(name)
            bdl_sync.prioritize_names([], [name], priority=10)
            teams.append({"name": name, "rank": None, "maps": []})
            continue
        rank = statsdb.team_rank(row["id"])
        pool = statsdb.map_pool(row["id"])
        if not pool:
            bdl_sync.prioritize_names([], [name], priority=10)
        teams.append(
            {
                "name": row["name"],
                "rank": rank.get("rank") if rank else None,
                "maps": [
                    {
                        "label": map_label(p["map_name"]),
                        "played": p["matches_played"],
                        "wins": p["wins"],
                        "losses": p["losses"],
                        "win_rate": round((p["win_rate"] or 0) * (100 if (p["win_rate"] or 0) <= 1 else 1)),
                    }
                    for p in pool[:8]
                ],
            }
        )
    return {
        "ok": True,
        "label": " vs ".join(t["name"] for t in teams),
        "teams": teams,
        "queued": bool(missing),
        "message": f"Queued {', '.join(missing)}" if missing else None,
    }
