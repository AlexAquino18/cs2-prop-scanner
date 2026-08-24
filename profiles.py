"""Player / matchup payloads from the local BallDontLie database."""
from __future__ import annotations

from teams import split_matchup, team_key
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

ACTIVE_POOL = (
    "de_ancient",
    "de_anubis",
    "de_cache",
    "de_dust2",
    "de_inferno",
    "de_mirage",
    "de_nuke",
)
POOL_GAMES = 20


def map_label(name: str | None) -> str:
    if not name:
        return "—"
    slug = canon_map(name)
    if slug:
        return MAP_LABELS.get(slug, slug.replace("de_", "").title())
    return MAP_LABELS.get(name, name.replace("de_", "").replace("_", " ").title())


def canon_map(name: str | None) -> str | None:
    raw = (name or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if raw.startswith("de"):
        raw = raw[2:]
    if raw in {"dustii", "dust2"}:
        raw = "dust2"
    slug = f"de_{raw}" if raw else ""
    return slug if slug in MAP_LABELS else None


def _blank_form() -> dict:
    return {"played": 0, "wins": 0, "losses": 0, "map1": 0, "map2": 0}


def recent_pool(team_id: int | None, games: int = POOL_GAMES) -> tuple[list[dict], int]:
    if not team_id:
        return [], 0
    series = statsdb.recent_final_match_ids(team_id, games)
    rows = statsdb.team_recent_maps(team_id, games)
    stats = {slug: _blank_form() for slug in ACTIVE_POOL}
    for row in rows:
        slug = canon_map(row.get("map_name"))
        if slug not in stats:
            continue
        st = stats[slug]
        st["played"] += 1
        num = row.get("map_number") or 0
        if num == 1:
            st["map1"] += 1
        elif num == 2:
            st["map2"] += 1
        winner = row.get("winner_id")
        if winner == team_id:
            st["wins"] += 1
        elif winner:
            st["losses"] += 1
    out = []
    enough = len(series) >= 8
    for slug in ACTIVE_POOL:
        st = stats[slug]
        played = st["played"]
        decided = st["wins"] + st["losses"]
        rate = round(100 * st["wins"] / decided) if decided else 0
        out.append(
            {
                "map": slug,
                "label": map_label(slug),
                "played": played,
                "wins": st["wins"],
                "losses": st["losses"],
                "win_rate": rate,
                "map1": st["map1"],
                "map2": st["map2"],
                "likely_ban": bool(enough and played == 0),
            }
        )
    out.sort(key=lambda m: (-m["played"], -m["win_rate"], m["label"]))
    return out, len(series)


def _project_maps(teams: list[dict]) -> dict | None:
    if len(teams) < 2:
        return None
    a, b = teams[0], teams[1]
    a_maps, b_maps = a.get("maps") or [], b.get("maps") or []
    if not a_maps or not b_maps:
        return None
    a_bans = {m["map"] for m in a_maps if m.get("likely_ban")}
    b_bans = {m["map"] for m in b_maps if m.get("likely_ban")}

    def choose(maps: list[dict], opp_bans: set[str], taken: set[str]) -> dict | None:
        ranked = sorted(
            maps,
            key=lambda m: (-(m.get("map1") or 0), -(m.get("played") or 0), -(m.get("win_rate") or 0)),
        )
        for row in ranked:
            if row["map"] in taken or row.get("likely_ban"):
                continue
            if row["map"] in opp_bans:
                continue
            if row.get("played"):
                return row
        for row in ranked:
            if row["map"] not in taken and not row.get("likely_ban"):
                return row
        return None

    first = choose(a_maps, b_bans, set())
    taken = {first["map"]} if first else set()
    second = choose(b_maps, a_bans, taken)
    if not first or not second:
        return None
    bans = []
    for team in teams:
        for row in team.get("maps") or []:
            if row.get("likely_ban"):
                bans.append({"team": team["name"], "map": row["label"]})
    return {
        "map1": {
            "label": first["label"],
            "why": f"{a['name']} pick",
        },
        "map2": {
            "label": second["label"],
            "why": f"{b['name']} pick",
        },
        "bans": bans,
        "note": "From last 20 games, not a live veto.",
    }


def _same_org(a: str | None, b: str | None) -> bool:
    ka, kb = team_key(a), team_key(b)
    return bool(ka and kb and ka == kb)


def _opp(row: dict, player_team_id: int | None, player_team_name: str | None = None) -> str:
    t1, t2 = row.get("team1_name") or "", row.get("team2_name") or ""
    id1, id2 = row.get("team1_id"), row.get("team2_id")
    if player_team_id:
        if id1 == player_team_id:
            return t2 or "—"
        if id2 == player_team_id:
            return t1 or "—"
    if player_team_name:
        on1 = _same_org(player_team_name, t1)
        on2 = _same_org(player_team_name, t2)
        if on1 and not on2:
            return t2 or "—"
        if on2 and not on1:
            return t1 or "—"
    if t1 and t2:
        return f"{t1} vs {t2}"
    return t2 or t1 or "—"


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


def _short_opp(name: str | None) -> str:
    raw = (name or "—").strip()
    if " vs " in raw:
        return "—"
    token = raw.split()[0] if raw else "—"
    return token[:10]


def _sample(
    row: dict,
    value: float,
    maps: str,
    team_id: int | None,
    line: float | None,
    team_name: str | None = None,
) -> dict:
    opponent = _opp(row, team_id, team_name)
    return {
        "start": row.get("start_time"),
        "opponent": opponent,
        "opp": _short_opp(opponent),
        "maps": maps,
        "value": value,
        "kills": row.get("kills"),
        "deaths": row.get("deaths"),
        "assists": row.get("assists"),
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
    team_name: str | None = None,
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
        out.append(_sample(row, val, maps, team_id, line, team_name))
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
    team_name: str | None = None,
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
        row = _sample(first, round(sum(vals), 1), label, team_id, line, team_name)
        row["kills"] = sum(m.get("kills") or 0 for m in maps)
        row["deaths"] = sum(m.get("deaths") or 0 for m in maps)
        row["assists"] = sum(m.get("assists") or 0 for m in maps)
        row["first_kills"] = sum(m.get("first_kills") or 0 for m in maps)
        row["adr"] = round(sum((m.get("adr") or 0) for m in maps) / len(maps), 1)
        row["hs_pct"] = round(sum((m.get("hs_pct") or 0) for m in maps) / len(maps), 1)
        out.append(row)
        if len(out) >= 10:
            break
    return out


def _match_samples(
    matches: list[dict],
    stat_key: str,
    team_id: int | None,
    line: float | None,
    limit: int = 10,
    team_name: str | None = None,
) -> list[dict]:
    out = []
    for row in matches:
        val = _stat_value(row, stat_key)
        if val is None:
            continue
        maps = f"BO{row.get('best_of') or '?'}"
        out.append(_sample(row, val, maps, team_id, line, team_name))
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


def _hit_rate(samples: list[dict], line: float | None, n: int | None = None) -> dict | None:
    chunk = samples[:n] if n else samples
    if line is None or not chunk:
        return None
    overs = sum(1 for s in chunk if s.get("vs") == "over")
    return {
        "line": line,
        "overs": overs,
        "n": len(chunk),
        "pct": round(100 * overs / len(chunk)),
    }


def player_profile(
    name: str,
    stat_key: str = "kills",
    map_range: str = "1-2",
    line: float | None = None,
    team: str | None = None,
) -> dict:
    statsdb.init_db()
    player, err, queued = bdl_sync.lookup_or_fetch_player(name, team=team)
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
    grain = "game"
    side_name = player.get("team_name") or team
    side_id = player.get("team_id")
    if not side_id and side_name:
        hit = statsdb.find_team(side_name, team_key)
        if hit:
            side_id = hit["id"]
    if token == "1":
        samples = _map_samples(maps, stat_key, side_id, line, map_number=1, team_name=side_name)
    elif token in {"1-2", "1-3"}:
        n = 2 if token == "1-2" else 3
        samples = _series_sums(maps, stat_key, n, side_id, line, complete_only=True, team_name=side_name)
        grain = "series"
    else:
        samples = _series_sums(maps, stat_key, None, side_id, line, complete_only=False, team_name=side_name)
        grain = "series"
        if not samples:
            samples = _match_samples(matches, stat_key, side_id, line, team_name=side_name)
    target = 10
    match_ids = statsdb.recent_final_match_ids(side_id, 15) if side_id else []
    unsynced = len(statsdb.unsynced_map_ids_for_matches(match_ids, 99)) if match_ids else 0
    filling = (side_id and not match_ids) or (unsynced > 0 and len(samples) < target)
    if filling:
        note = (
            f"Loaded {len(samples)} of last {target} games — filling the rest…"
            if samples
            else "Pulling last 10 games…"
        )
    hits = _hit_rate(samples, line)
    avg = round(sum(s["value"] for s in samples) / len(samples), 1) if samples else None
    rank = statsdb.team_rank(side_id)
    pool, _n = recent_pool(side_id)
    cached_at = None
    if side_id:
        cached_at = statsdb.meta_get(f"ready_at:{side_id}") or statsdb.meta_get(f"pool_at:{side_id}")
    cached_at = cached_at or statsdb.meta_get("last_daily")
    return {
        "ok": True,
        "queued": filling,
        "status": "ready" if samples and not filling else "loading",
        "player": player["nickname"],
        "full_name": player.get("full_name"),
        "team": side_name or player.get("team_name"),
        "age": player.get("age"),
        "rank": rank.get("rank") if rank else None,
        "stat": _stat_title(stat_key, token),
        "grain": grain,
        "line": line,
        "avg": avg,
        "hits": hits,
        "l5": _hit_rate(samples, line, 5),
        "l10": _hit_rate(samples, line, 10),
        "maps": pool,
        "recent": samples[:target],
        "cached_at": cached_at,
        "message": note,
    }


def matchup_profile(label: str, sides_text: str | None = None) -> dict:
    statsdb.init_db()
    sides = [p.strip() for p in (sides_text or "").split("|") if p.strip()]
    if len(sides) < 2:
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
        pool, series_n = recent_pool(row["id"])
        teams.append(
            {
                "name": row["name"],
                "rank": rank.get("rank") if rank else None,
                "maps": pool,
                "series": series_n,
                "queued": series_n == 0,
            }
        )
    empty = [t["name"] for t in teams if not t["maps"] or t.get("queued")]
    projected = _project_maps(teams)
    return {
        "ok": True,
        "label": " vs ".join(t["name"] for t in teams),
        "teams": teams,
        "projected": projected,
        "queued": bool(empty),
        "status": "ready" if not empty else "loading",
        "message": "Pulling recent maps…" if empty else None,
    }
