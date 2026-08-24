"""Background BallDontLie sync. One API call per tick so the trial limit holds."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from difflib import SequenceMatcher
from normalize import normalize_player_name
from sources import bdl
from teams import team_key
import statsdb

_LOCK = threading.Lock()
_HYDRATING: set[int] = set()
_STATE = {
    "running": False,
    "last_error": None,
    "last_job": None,
}


def status() -> dict:
    statsdb.init_db()
    out = {"enabled": bdl.enabled(), **statsdb.counts(), **_STATE}
    return out


def lookup_or_fetch_player(name: str) -> tuple[dict | None, str | None, bool]:
    """Load a player from SQLite, or search BallDontLie immediately."""
    statsdb.init_db()
    key = normalize_player_name(name)
    hit = statsdb.find_player(key)
    if hit:
        statsdb.clear_miss(key)
        statsdb.pop_hydrate(key)
        _kick_hydrate(hit)
        return hit, None, False
    if statsdb.is_miss(key):
        return None, f"No stats listing for {name}. A lot of T3 and mixer names are not in this database.", False
    if not bdl.enabled():
        return None, "Player stats are not connected on this server.", False
    statsdb.want_hydrate(key)
    got = _LOCK.acquire(timeout=0.8)
    if not got:
        statsdb.enqueue("player_search", {"q": name}, priority=5)
        return None, "Looking this player up…", True
    try:
        data = bdl.get("/players", [("search", name), ("per_page", "25")], wait_budget=0.3)
    except (bdl.BdlBusy, bdl.BdlError, Exception):
        statsdb.enqueue("player_search", {"q": name}, priority=5)
        return None, "Looking this player up…", True
    finally:
        _LOCK.release()
    hit = _ingest_search_rows(name, data.get("data") or [])
    if not hit:
        statsdb.mark_miss(key)
        return None, f"No stats listing for {name}. A lot of T3 and mixer names are not in this database.", False
    statsdb.clear_miss(key)
    statsdb.pop_hydrate(key)
    _kick_hydrate(hit)
    return hit, None, False


def lookup_or_fetch_team(name: str) -> tuple[dict | None, bool]:
    statsdb.init_db()
    hit = statsdb.find_team(name, team_key)
    if hit:
        if hit.get("id"):
            if not statsdb.map_pool(hit["id"]):
                statsdb.enqueue("map_pool", {"team_id": hit["id"]}, priority=8)
        return hit, False
    statsdb.enqueue("team_by_key", {"team": name, "key": team_key(name)}, priority=8)
    return None, True


def _best_search_hit(query: str, rows: list[dict]) -> dict | None:
    key = normalize_player_name(query)
    if not key:
        return None
    hit = statsdb.find_player(key)
    if hit:
        return hit
    best = None
    best_score = 0.0
    for row in rows:
        nick = row.get("nickname") or ""
        score = SequenceMatcher(None, key, normalize_player_name(nick)).ratio()
        if score > best_score:
            best, best_score = row, score
    if best and best_score >= 0.72:
        return statsdb.find_player(normalize_player_name(best.get("nickname") or ""))
    return None


def _ingest_search_rows(query: str, rows: list[dict]) -> dict | None:
    statsdb.upsert_players(rows, normalize_player_name)
    return _best_search_hit(query, rows)


def _kick_hydrate(player: dict) -> None:
    pid = player.get("id")
    team_id = player.get("team_id")
    if team_id and not statsdb.map_pool(team_id):
        statsdb.enqueue("map_pool", {"team_id": team_id}, priority=8)
    if not pid or pid in _HYDRATING:
        return
    if statsdb.player_map_rows(pid, limit=1):
        return
    team_id = player.get("team_id")
    if team_id:
        statsdb.enqueue("team_matches", {"team_id": team_id}, priority=5)
        statsdb.enqueue("map_pool", {"team_id": team_id}, priority=8)
    _HYDRATING.add(pid)
    threading.Thread(
        target=_hydrate_player,
        args=(player,),
        daemon=True,
        name=f"bdl-hydrate-{pid}",
    ).start()


def _hydrate_player(player: dict) -> None:
    pid = player.get("id")
    try:
        if not bdl.enabled():
            return
        tid = player.get("team_id")
        if not tid:
            return
        with _LOCK:
            _STATE["running"] = True
            try:
                _STATE["last_job"] = _run_kind("team_matches", {"team_id": tid})
                ids = statsdb.recent_final_match_ids(tid, 6)
                for mid in statsdb.next_match_stats_ids_for_team(tid, 2):
                    _STATE["last_job"] = _run_kind("match_stats", {"match_id": mid})
                    if statsdb.player_match_rows(pid, limit=1):
                        break
                if ids:
                    _STATE["last_job"] = _run_kind("match_maps", {"ids": ids})
                for mid in statsdb.unsynced_map_ids_for_matches(ids, 8):
                    _STATE["last_job"] = _run_kind("map_stats", {"match_map_id": mid})
                    if statsdb.player_map_rows(pid, limit=1):
                        break
                if not statsdb.map_pool(tid):
                    _STATE["last_job"] = _run_kind("map_pool", {"team_id": tid})
                _STATE["last_error"] = None
            except Exception as exc:
                _STATE["last_error"] = str(exc)
            finally:
                _STATE["running"] = False
    finally:
        if pid:
            _HYDRATING.discard(pid)


def prioritize_names(player_names: list[str], team_names: list[str], priority: int = 12) -> None:
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
    job = statsdb.pop_job(max_priority=15)
    if not job:
        mid = statsdb.next_map_stats_id()
        if mid:
            job = ("map_stats", {"match_map_id": mid})
        else:
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
    return _run_kind(kind, payload)


def _run_kind(kind: str, payload: dict) -> str:
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
        hit = _ingest_search_rows(q, rows)
        key = normalize_player_name(q)
        if hit:
            statsdb.clear_miss(key)
            if statsdb.pop_hydrate(key):
                _kick_hydrate(hit)
        elif not rows:
            statsdb.mark_miss(key)
        return f"search {q} +{len(rows)}"
    if kind == "team_by_key":
        name = payload.get("team") or ""
        data = bdl.get("/teams", [("search", name), ("per_page", "25")])
        rows = data.get("data") or []
        statsdb.upsert_teams(rows, team_key)
        hit = statsdb.find_team(name, team_key)
        if hit:
            statsdb.enqueue("map_pool", {"team_id": hit["id"]}, priority=8)
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
            elif "429" in str(note):
                threading.Event().wait(60)
        except Exception:
            threading.Event().wait(20)


def start_background() -> None:
    if not bdl.enabled():
        return
    statsdb.init_db()
    statsdb.drop_jobs("players_page", "teams_page")
    t = threading.Thread(target=loop, daemon=True, name="bdl-sync")
    t.start()
