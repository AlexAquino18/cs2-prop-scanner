"""Background BallDontLie sync. One API call per tick so the trial limit holds."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from difflib import SequenceMatcher
from normalize import normalize_player_name
from sources import bdl
from teams import team_key
import statsdb

MAPS_TARGET = 12
STALE_HOURS = 20
POOL_STALE_HOURS = 24 * 7

_LOCK = threading.Lock()
_STATE = {
    "running": False,
    "last_error": None,
    "last_job": None,
}


def status() -> dict:
    statsdb.init_db()
    out = {
        "enabled": bdl.enabled(),
        **statsdb.counts(),
        **_STATE,
        "last_daily": statsdb.meta_get("last_daily"),
    }
    return out


def _boost_team(name: str | None, team_id: int | None = None) -> None:
    if not name and not team_id:
        return
    payload = {"team": name or "", "_pri": 2}
    if team_id:
        payload["team_id"] = team_id
    statsdb.enqueue("team_refresh", payload, priority=2)


def lookup_or_fetch_player(name: str, team: str | None = None) -> tuple[dict | None, str | None, bool]:
    """Read SQLite first. If the name is missing, search now (or jump the queue)."""
    statsdb.init_db()
    key = normalize_player_name(name)
    hit = statsdb.find_player(key)
    if hit:
        _boost_team(hit.get("team_name") or team, hit.get("team_id"))
        if team:
            _boost_team(team)
        return hit, None, False
    if statsdb.is_miss(key):
        return None, f"No stats listing for {name}. A lot of T3 and mixer names are not in this database.", False
    if not bdl.enabled():
        return None, "Player stats are not connected on this server.", False
    if team:
        _boost_team(team)
    flag = f"searching:{key}"
    if not statsdb.meta_get(flag):
        statsdb.meta_set(flag, datetime.now(timezone.utc).isoformat())
        try:
            data = bdl.get("/players", [("search", name), ("per_page", "25")], wait_budget=0.4)
            rows = data.get("data") or []
            hit = _ingest_search_rows(name, rows)
            if hit:
                statsdb.clear_miss(key)
                statsdb.meta_delete(flag)
                _boost_team(hit.get("team_name") or team, hit.get("team_id"))
                return hit, None, False
            if not rows:
                statsdb.mark_miss(key)
                statsdb.meta_delete(flag)
                return None, f"No stats listing for {name}. A lot of T3 and mixer names are not in this database.", False
        except (bdl.BdlBusy, bdl.BdlError):
            pass
        except Exception:
            pass
    statsdb.enqueue("player_search", {"q": name}, priority=1)
    return None, "Looking this player up…", True


def lookup_or_fetch_team(name: str) -> tuple[dict | None, bool]:
    statsdb.init_db()
    hit = statsdb.find_team(name, team_key)
    if hit:
        if not statsdb.map_pool(hit["id"]):
            _boost_team(name, hit["id"])
        return hit, False
    if not bdl.enabled():
        return None, False
    flag = f"tsearch:{team_key(name) or name.lower()}"
    if not statsdb.meta_get(flag):
        statsdb.meta_set(flag, datetime.now(timezone.utc).isoformat())
        try:
            data = bdl.get("/teams", [("search", name), ("per_page", "25")], wait_budget=0.4)
            statsdb.upsert_teams(data.get("data") or [], team_key)
            hit = statsdb.find_team(name, team_key)
            if hit:
                statsdb.meta_delete(flag)
                _boost_team(name, hit["id"])
                return hit, False
        except (bdl.BdlBusy, bdl.BdlError):
            pass
        except Exception:
            pass
    _boost_team(name)
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


def schedule_board(player_names: list[str], team_names: list[str], force: bool = False) -> None:
    """Queue last-10-maps + map-pool refresh for everyone on the live board."""
    statsdb.init_db()
    for name in team_names[:30]:
        if not name:
            continue
        if force:
            statsdb.meta_set(f"stale:{team_key(name)}", "1")
        statsdb.enqueue("team_refresh", {"team": name, "_pri": 8}, priority=8)
    missing = 0
    for name in player_names:
        if missing >= 40:
            break
        if not name:
            continue
        if statsdb.find_player(normalize_player_name(name)):
            continue
        statsdb.enqueue("player_search", {"q": name}, priority=11)
        missing += 1


def prioritize_names(player_names: list[str], team_names: list[str], priority: int = 12) -> None:
    schedule_board(player_names, team_names, force=False)


def schedule_catalog() -> None:
    """Queue every known team so map pools and last maps fill in the background."""
    statsdb.init_db()
    for row in statsdb.list_teams():
        statsdb.enqueue(
            "team_refresh",
            {"team": row.get("name") or "", "team_id": row["id"], "_pri": 18},
            priority=18,
        )


def daily_refresh() -> None:
    """Look for new matches only. Existing map stats and pools stay put."""
    if not bdl.enabled():
        return
    statsdb.init_db()
    players, teams = _board_names()
    schedule_board(players, teams, force=False)
    schedule_catalog()
    if not statsdb.meta_get("rankings_at") or statsdb.is_stale("rankings_at", STALE_HOURS):
        statsdb.enqueue("rankings", {}, priority=25)
    statsdb.meta_set("last_daily", datetime.now(timezone.utc).isoformat())


def _board_names() -> tuple[list[str], list[str]]:
    try:
        import store

        latest = store.latest_snapshot_ids(1)
        if not latest:
            return [], []
        lines = store.load_lines(latest[0][0])
    except Exception:
        return [], []
    players = list(dict.fromkeys(p.player_raw for p in lines if p.player_raw))
    teams = list(dict.fromkeys(p.team for p in lines if p.team))
    return players, teams


def _continue(payload: dict) -> None:
    pri = int(payload.get("_pri") or 8)
    statsdb.enqueue("team_refresh", payload, priority=pri)


def _refresh_team(payload: dict) -> str:
    name = payload.get("team") or ""
    tid = payload.get("team_id")
    pri = int(payload.get("_pri") or 8)
    hit = statsdb.find_team(name, team_key) if name else None
    if not hit and tid:
        hit = statsdb.find_team_by_id(tid)
        if hit:
            name = hit.get("name") or name
    if not hit:
        data = bdl.get("/teams", [("search", name), ("per_page", "25")])
        statsdb.upsert_teams(data.get("data") or [], team_key)
        hit = statsdb.find_team(name, team_key)
        if hit:
            _continue({"team": name, "team_id": hit["id"], "_pri": pri})
        return f"team search {name}"
    tid = hit["id"]
    nxt = {"team": name or hit.get("name"), "team_id": tid, "_pri": pri}
    pool_key = f"pool_at:{tid}"
    match_key = f"matches_at:{tid}"
    if not statsdb.map_pool(tid) and not payload.get("did_pool"):
        _run_kind("map_pool", {"team_id": tid})
        statsdb.meta_set(pool_key, datetime.now(timezone.utc).isoformat())
        nxt["did_pool"] = True
        _continue(nxt)
        return f"pool {tid}"
    nxt["did_pool"] = True
    if not payload.get("did_matches") and (
        not statsdb.recent_final_match_ids(tid, 1) or statsdb.is_stale(match_key, STALE_HOURS)
    ):
        _run_kind("team_matches", {"team_id": tid})
        statsdb.meta_set(match_key, datetime.now(timezone.utc).isoformat())
        nxt["did_matches"] = True
        _continue(nxt)
        return f"matches {tid}"
    nxt["did_matches"] = True
    ids = statsdb.recent_final_match_ids(tid, 8)
    missing_maps = statsdb.matches_missing_map_rows(ids)
    if missing_maps:
        _run_kind("match_maps", {"ids": missing_maps})
        _continue(nxt)
        return f"maps {tid}"
    nxt_ids = statsdb.unsynced_map_ids_for_matches(ids, 1)
    if nxt_ids:
        have = statsdb.team_synced_map_count(tid, ids)
        _run_kind("map_stats", {"match_map_id": nxt_ids[0]})
        _continue(nxt)
        return f"map stats {tid} {have + 1}"
    if statsdb.map_pool(tid) and not statsdb.meta_get(pool_key):
        statsdb.meta_set(pool_key, datetime.now(timezone.utc).isoformat())
    elif statsdb.meta_get(pool_key) and statsdb.is_stale(pool_key, POOL_STALE_HOURS):
        _run_kind("map_pool", {"team_id": tid})
        statsdb.meta_set(pool_key, datetime.now(timezone.utc).isoformat())
        _continue(nxt)
        return f"pool {tid}"
    have = statsdb.team_synced_map_count(tid, ids)
    statsdb.meta_set(f"ready_at:{tid}", datetime.now(timezone.utc).isoformat())
    return f"ready {name or tid} maps={have}"


def kick_catalog() -> None:
    statsdb.init_db()
    if not statsdb.meta_get("teams_done"):
        cursor = statsdb.meta_get("teams_cursor")
        statsdb.enqueue("teams_page", {"cursor": cursor} if cursor else {}, priority=41)
    if not statsdb.meta_get("rankings_at"):
        statsdb.enqueue("rankings", {}, priority=30)


def _params(cursor: str | None, extra: list[tuple] | None = None) -> list[tuple]:
    items = [("per_page", "100")]
    if cursor:
        items.append(("cursor", cursor))
    if extra:
        items.extend(extra)
    return items


def _tick_job() -> str:
    job = statsdb.pop_job()
    if not job:
        kick_catalog()
        job = statsdb.pop_job()
    if not job:
        return "idle"
    kind, payload = job
    return _run_kind(kind, payload)


def _run_kind(kind: str, payload: dict) -> str:
    if kind == "team_refresh":
        return _refresh_team(payload)
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
        for row in rows:
            tid = row.get("id")
            if tid:
                statsdb.enqueue(
                    "team_refresh",
                    {"team": row.get("name") or "", "team_id": tid, "_pri": 18},
                    priority=18,
                )
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
            statsdb.meta_delete(f"searching:{key}")
            tid = hit.get("team_id")
            if tid:
                statsdb.enqueue(
                    "team_refresh",
                    {"team": hit.get("team_name") or q, "team_id": tid},
                    priority=2,
                )
        elif not rows:
            statsdb.mark_miss(key)
            statsdb.meta_delete(f"searching:{key}")
        return f"search {q} +{len(rows)}"
    if kind == "team_by_key":
        name = payload.get("team") or ""
        data = bdl.get("/teams", [("search", name), ("per_page", "25")])
        rows = data.get("data") or []
        statsdb.upsert_teams(rows, team_key)
        hit = statsdb.find_team(name, team_key)
        if hit:
            statsdb.enqueue("team_refresh", {"team": name, "team_id": hit["id"]}, priority=8)
        return f"team {name}"
    if kind == "team_matches":
        tid = payload["team_id"]
        data = bdl.get("/matches", [("team_ids[]", tid), ("per_page", "25")])
        rows = data.get("data") or []
        statsdb.upsert_matches(rows)
        ids = [r["id"] for r in rows if r.get("status_state") == "final"][:8]
        if ids:
            statsdb.enqueue("match_maps", {"ids": ids}, priority=6)
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
        nested = [r.get("player") for r in rows if (r.get("player") or {}).get("id")]
        if nested:
            statsdb.upsert_players(nested, normalize_player_name)
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
                threading.Event().wait(max(1.0, min(20.0, bdl.time_until_slot() or 2.0)))
        except Exception:
            threading.Event().wait(20)


def start_background() -> None:
    if not bdl.enabled():
        return
    statsdb.init_db()
    statsdb.drop_jobs("players_page", "team_by_key")
    statsdb.drop_jobs_from("player_search", 4)
    kick_catalog()
    schedule_catalog()
    if statsdb.is_stale("last_daily", STALE_HOURS):
        daily_refresh()
    t = threading.Thread(target=loop, daemon=True, name="bdl-sync")
    t.start()
