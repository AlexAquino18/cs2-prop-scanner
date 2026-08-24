"""Background BallDontLie sync. One API call per tick so the trial limit holds."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from normalize import normalize_player_name
from sources import bdl
from teams import team_key
import statsdb

_LOCK = threading.Lock()
_STATE = {
    "running": False,
    "last_error": None,
    "last_job": None,
}


def status() -> dict:
    statsdb.init_db()
    out = {"enabled": bdl.enabled(), **statsdb.counts(), **_STATE}
    return out


def prioritize_names(player_names: list[str], team_names: list[str], priority: int = 22) -> None:
    statsdb.init_db()
    for name in team_names:
        key = team_key(name)
        if key:
            statsdb.enqueue("team_by_key", {"team": name, "key": key}, priority=priority)
    for name in player_names:
        if name:
            statsdb.enqueue("player_search", {"q": name}, priority=priority)


def kick_catalog() -> None:
    statsdb.init_db()
    if not statsdb.meta_get("players_done"):
        statsdb.enqueue("players_page", {"cursor": statsdb.meta_get("players_cursor")}, priority=40)
    if not statsdb.meta_get("teams_done"):
        statsdb.enqueue("teams_page", {"cursor": statsdb.meta_get("teams_cursor")}, priority=41)
    rankings_at = statsdb.meta_get("rankings_at")
    if not rankings_at:
        statsdb.enqueue("rankings", {}, priority=30)


def _params(cursor: str | None, extra: list[tuple] | None = None) -> list[tuple]:
    items = [("per_page", "100")]
    if cursor:
        items.append(("cursor", cursor))
    if extra:
        items.extend(extra)
    return items


def _tick_job() -> str:
    mid = statsdb.next_map_stats_id()
    if mid:
        job = ("map_stats", {"match_map_id": mid})
    else:
        job = statsdb.pop_job(max_priority=19)
        if not job:
            ids = statsdb.next_maps_job_ids()
            if ids:
                job = ("match_maps", {"ids": ids})
            else:
                sid = statsdb.next_match_stats_id()
                if sid:
                    job = ("match_stats", {"match_id": sid})
                else:
                    job = statsdb.pop_job()
    if not job:
        kick_catalog()
        job = statsdb.pop_job()
    if not job:
        return "idle"

    kind, payload = job
    if kind == "players_page":
        data = bdl.get("/players", _params(payload.get("cursor"), [("active", "true")]))
        rows = data.get("data") or []
        statsdb.upsert_players(rows, normalize_player_name)
        nxt = (data.get("meta") or {}).get("next_cursor")
        if nxt:
            statsdb.meta_set("players_cursor", str(nxt))
            statsdb.enqueue("players_page", {"cursor": str(nxt)}, priority=40)
        else:
            statsdb.meta_set("players_done", "1")
        return f"players +{len(rows)}"
    if kind == "teams_page":
        data = bdl.get("/teams", _params(payload.get("cursor")))
        rows = data.get("data") or []
        statsdb.upsert_teams(rows, team_key)
        nxt = (data.get("meta") or {}).get("next_cursor")
        if nxt:
            statsdb.meta_set("teams_cursor", str(nxt))
            statsdb.enqueue("teams_page", {"cursor": str(nxt)}, priority=41)
        else:
            statsdb.meta_set("teams_done", "1")
        return f"teams +{len(rows)}"
    if kind == "rankings":
        data = bdl.get("/rankings")
        rows = data.get("data") or []
        statsdb.replace_rankings(rows)
        statsdb.meta_set("rankings_at", datetime.now(timezone.utc).isoformat())
        return f"rankings {len(rows)}"
    if kind == "player_search":
        q = payload.get("q") or ""
        data = bdl.get("/players", [("search", q), ("per_page", "25")])
        rows = data.get("data") or []
        statsdb.upsert_players(rows, normalize_player_name)
        for row in rows:
            team = row.get("team") or {}
            if team.get("id"):
                statsdb.enqueue("team_matches", {"team_id": team["id"]}, priority=18)
                statsdb.enqueue("map_pool", {"team_id": team["id"]}, priority=25)
        return f"search {q} +{len(rows)}"
    if kind == "team_by_key":
        name = payload.get("team") or ""
        data = bdl.get("/teams", [("search", name), ("per_page", "25")])
        rows = data.get("data") or []
        statsdb.upsert_teams(rows, team_key)
        hit = statsdb.find_team(name, team_key)
        if hit:
            statsdb.enqueue("team_matches", {"team_id": hit["id"]}, priority=18)
            statsdb.enqueue("map_pool", {"team_id": hit["id"]}, priority=25)
        return f"team {name}"
    if kind == "team_matches":
        tid = payload["team_id"]
        data = bdl.get("/matches", [("team_ids[]", tid), ("per_page", "25")])
        rows = data.get("data") or []
        statsdb.upsert_matches(rows)
        ids = [r["id"] for r in rows if r.get("status_state") == "final"][:8]
        if ids:
            statsdb.enqueue("match_maps", {"ids": ids}, priority=16)
        return f"matches team {tid} +{len(rows)}"
    if kind == "match_maps":
        ids = payload.get("ids") or []
        params = [("match_ids[]", i) for i in ids]
        data = bdl.get("/match_maps", params)
        rows = data.get("data") or []
        statsdb.upsert_match_maps(rows)
        return f"maps +{len(rows)}"
    if kind == "map_stats":
        mid = payload["match_map_id"]
        data = bdl.get("/player_match_map_stats", [("match_map_id", mid)])
        rows = data.get("data") or []
        statsdb.upsert_player_map_stats(mid, rows)
        return f"map stats {mid} +{len(rows)}"
    if kind == "match_stats":
        mid = payload["match_id"]
        data = bdl.get("/player_match_stats", [("match_id", mid)])
        rows = data.get("data") or []
        statsdb.upsert_player_match_stats(mid, rows)
        return f"match stats {mid} +{len(rows)}"
    if kind == "map_pool":
        tid = payload["team_id"]
        data = bdl.get("/team_map_pool", [("team_id", tid)])
        rows = data.get("data") or []
        statsdb.replace_map_pool(tid, rows)
        return f"pool {tid}"
    return f"skip {kind}"


def tick() -> str:
    if not bdl.enabled():
        return "disabled"
    statsdb.init_db()
    with _LOCK:
        _STATE["running"] = True
        try:
            note = _tick_job()
            _STATE["last_job"] = note
            _STATE["last_error"] = None
            return note
        except Exception as exc:
            _STATE["last_error"] = str(exc)
            return f"error: {exc}"
        finally:
            _STATE["running"] = False


def loop() -> None:
    kick_catalog()
    while True:
        try:
            note = tick()
            if note == "idle" or note == "disabled":
                threading.Event().wait(20)
        except Exception:
            threading.Event().wait(20)


def start_background() -> None:
    if not bdl.enabled():
        return
    t = threading.Thread(target=loop, daemon=True, name="bdl-sync")
    t.start()
