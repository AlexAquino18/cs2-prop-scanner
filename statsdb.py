"""SQLite tables for the BallDontLie player / match database."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from store import _connect, init_db as init_line_db


def init_db() -> None:
    init_line_db()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bdl_meta (
                k TEXT PRIMARY KEY,
                v TEXT
            );
            CREATE TABLE IF NOT EXISTS bdl_teams (
                id INTEGER PRIMARY KEY,
                name TEXT,
                slug TEXT,
                short_name TEXT,
                team_key TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_bdl_teams_key ON bdl_teams(team_key);
            CREATE TABLE IF NOT EXISTS bdl_players (
                id INTEGER PRIMARY KEY,
                nickname TEXT,
                full_name TEXT,
                player_key TEXT,
                team_id INTEGER,
                team_name TEXT,
                age INTEGER,
                is_active INTEGER,
                steam_id TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_bdl_players_key ON bdl_players(player_key);
            CREATE TABLE IF NOT EXISTS bdl_rankings (
                team_id INTEGER PRIMARY KEY,
                rank INTEGER,
                points REAL,
                ranking_type TEXT,
                ranking_date TEXT
            );
            CREATE TABLE IF NOT EXISTS bdl_map_pool (
                team_id INTEGER,
                map_name TEXT,
                matches_played INTEGER,
                wins INTEGER,
                losses INTEGER,
                win_rate REAL,
                is_permaban INTEGER,
                PRIMARY KEY (team_id, map_name)
            );
            CREATE TABLE IF NOT EXISTS bdl_matches (
                id INTEGER PRIMARY KEY,
                start_time TEXT,
                status_state TEXT,
                best_of INTEGER,
                team1_id INTEGER,
                team2_id INTEGER,
                team1_name TEXT,
                team2_name TEXT,
                team1_score INTEGER,
                team2_score INTEGER,
                tournament TEXT,
                maps_synced INTEGER DEFAULT 0,
                match_stats_synced INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS bdl_match_maps (
                id INTEGER PRIMARY KEY,
                match_id INTEGER,
                map_name TEXT,
                map_number INTEGER,
                team1_score INTEGER,
                team2_score INTEGER,
                winner_id INTEGER,
                stats_synced INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS bdl_player_map_stats (
                player_id INTEGER,
                match_map_id INTEGER,
                kills INTEGER,
                deaths INTEGER,
                assists INTEGER,
                adr REAL,
                kast REAL,
                rating REAL,
                hs_pct REAL,
                first_kills INTEGER,
                first_deaths INTEGER,
                clutches INTEGER,
                PRIMARY KEY (player_id, match_map_id)
            );
            CREATE TABLE IF NOT EXISTS bdl_player_match_stats (
                player_id INTEGER,
                match_id INTEGER,
                team_id INTEGER,
                kills INTEGER,
                deaths INTEGER,
                assists INTEGER,
                adr REAL,
                kast REAL,
                rating REAL,
                hs_pct REAL,
                first_kills INTEGER,
                first_deaths INTEGER,
                clutches INTEGER,
                PRIMARY KEY (player_id, match_id)
            );
            CREATE TABLE IF NOT EXISTS bdl_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 50,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bdl_jobs_pri ON bdl_jobs(priority, id);
            """
        )


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def meta_get(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT v FROM bdl_meta WHERE k = ?", (key,)).fetchone()
    return row["v"] if row else default


def meta_set(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO bdl_meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, value),
        )


def enqueue(kind: str, payload: dict, priority: int = 50) -> None:
    blob = json.dumps(payload, sort_keys=True)
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, priority FROM bdl_jobs WHERE kind = ? AND payload = ?",
            (kind, blob),
        ).fetchone()
        if row:
            if priority < int(row["priority"]):
                conn.execute(
                    "UPDATE bdl_jobs SET priority = ? WHERE id = ?",
                    (priority, row["id"]),
                )
            return
        conn.execute(
            "INSERT INTO bdl_jobs (kind, payload, priority, created_at) VALUES (?, ?, ?, ?)",
            (kind, blob, priority, now()),
        )


def drop_jobs(*kinds: str) -> None:
    if not kinds:
        return
    placeholders = ",".join("?" * len(kinds))
    with _connect() as conn:
        conn.execute(f"DELETE FROM bdl_jobs WHERE kind IN ({placeholders})", kinds)


def pop_job(max_priority: int | None = None) -> tuple[str, dict] | None:
    with _connect() as conn:
        if max_priority is None:
            row = conn.execute(
                "SELECT id, kind, payload FROM bdl_jobs ORDER BY priority ASC, id ASC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, kind, payload FROM bdl_jobs
                WHERE priority <= ?
                ORDER BY priority ASC, id ASC LIMIT 1
                """,
                (max_priority,),
            ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM bdl_jobs WHERE id = ?", (row["id"],))
        return row["kind"], json.loads(row["payload"])


def counts() -> dict:
    with _connect() as conn:
        def n(table: str) -> int:
            return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        return {
            "players": n("bdl_players"),
            "teams": n("bdl_teams"),
            "matches": n("bdl_matches"),
            "maps": n("bdl_match_maps"),
            "map_stats": n("bdl_player_map_stats"),
            "jobs": n("bdl_jobs"),
        }


def upsert_teams(rows: list[dict], team_key_fn) -> None:
    ts = now()
    with _connect() as conn:
        for row in rows:
            name = row.get("name") or ""
            short = row.get("short_name") or ""
            conn.execute(
                """
                INSERT INTO bdl_teams (id, name, slug, short_name, team_key, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, slug=excluded.slug, short_name=excluded.short_name,
                    team_key=excluded.team_key, updated_at=excluded.updated_at
                """,
                (
                    row["id"],
                    name,
                    row.get("slug"),
                    short,
                    team_key_fn(short or name),
                    ts,
                ),
            )


def upsert_players(rows: list[dict], player_key_fn) -> None:
    ts = now()
    with _connect() as conn:
        for row in rows:
            team = row.get("team") or {}
            nick = row.get("nickname") or ""
            conn.execute(
                """
                INSERT INTO bdl_players (
                    id, nickname, full_name, player_key, team_id, team_name,
                    age, is_active, steam_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    nickname=excluded.nickname, full_name=excluded.full_name,
                    player_key=excluded.player_key, team_id=excluded.team_id,
                    team_name=excluded.team_name, age=excluded.age,
                    is_active=excluded.is_active, steam_id=excluded.steam_id,
                    updated_at=excluded.updated_at
                """,
                (
                    row["id"],
                    nick,
                    row.get("full_name"),
                    player_key_fn(nick),
                    team.get("id"),
                    team.get("name") or team.get("short_name"),
                    row.get("age"),
                    1 if row.get("is_active") else 0,
                    row.get("steam_id"),
                    ts,
                ),
            )


def replace_rankings(rows: list[dict]) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM bdl_rankings")
        for row in rows:
            team = row.get("team") or {}
            tid = team.get("id")
            if not tid:
                continue
            conn.execute(
                """
                INSERT INTO bdl_rankings (team_id, rank, points, ranking_type, ranking_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tid, row.get("rank"), row.get("points"), row.get("ranking_type"), row.get("ranking_date")),
            )


def replace_map_pool(team_id: int, rows: list[dict]) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM bdl_map_pool WHERE team_id = ?", (team_id,))
        for row in rows:
            played = row.get("matches_played")
            if not played:
                continue
            conn.execute(
                """
                INSERT INTO bdl_map_pool (
                    team_id, map_name, matches_played, wins, losses, win_rate, is_permaban
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    row.get("map_name"),
                    played,
                    row.get("wins") or 0,
                    row.get("losses") or 0,
                    row.get("win_rate"),
                    1 if row.get("is_permaban") else 0,
                ),
            )


def upsert_matches(rows: list[dict]) -> None:
    with _connect() as conn:
        for row in rows:
            t1, t2 = row.get("team1") or {}, row.get("team2") or {}
            conn.execute(
                """
                INSERT INTO bdl_matches (
                    id, start_time, status_state, best_of, team1_id, team2_id,
                    team1_name, team2_name, team1_score, team2_score, tournament
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    start_time=excluded.start_time, status_state=excluded.status_state,
                    best_of=excluded.best_of, team1_score=excluded.team1_score,
                    team2_score=excluded.team2_score
                """,
                (
                    row["id"],
                    row.get("start_time"),
                    row.get("status_state"),
                    row.get("best_of"),
                    t1.get("id"),
                    t2.get("id"),
                    t1.get("name") or t1.get("short_name"),
                    t2.get("name") or t2.get("short_name"),
                    row.get("team1_score"),
                    row.get("team2_score"),
                    ((row.get("tournament") or {}).get("name")),
                ),
            )


def upsert_match_maps(rows: list[dict]) -> None:
    with _connect() as conn:
        match_ids = set()
        for row in rows:
            if not row.get("map_name"):
                continue
            match_ids.add(row.get("match_id"))
            conn.execute(
                """
                INSERT INTO bdl_match_maps (
                    id, match_id, map_name, map_number, team1_score, team2_score, winner_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    map_name=excluded.map_name, team1_score=excluded.team1_score,
                    team2_score=excluded.team2_score, winner_id=excluded.winner_id
                """,
                (
                    row["id"],
                    row.get("match_id"),
                    row.get("map_name"),
                    row.get("map_number"),
                    row.get("team1_score"),
                    row.get("team2_score"),
                    ((row.get("winner") or {}).get("id")),
                ),
            )
        for mid in match_ids:
            conn.execute("UPDATE bdl_matches SET maps_synced = 1 WHERE id = ?", (mid,))


def upsert_player_map_stats(match_map_id: int, rows: list[dict]) -> None:
    with _connect() as conn:
        for row in rows:
            player = row.get("player") or {}
            pid = player.get("id")
            if not pid:
                continue
            conn.execute(
                """
                INSERT INTO bdl_player_map_stats (
                    player_id, match_map_id, kills, deaths, assists, adr, kast,
                    rating, hs_pct, first_kills, first_deaths, clutches
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, match_map_id) DO UPDATE SET
                    kills=excluded.kills, deaths=excluded.deaths, assists=excluded.assists,
                    adr=excluded.adr, kast=excluded.kast, rating=excluded.rating,
                    hs_pct=excluded.hs_pct, first_kills=excluded.first_kills,
                    first_deaths=excluded.first_deaths, clutches=excluded.clutches
                """,
                (
                    pid, match_map_id,
                    row.get("kills"), row.get("deaths"), row.get("assists"),
                    row.get("adr"), row.get("kast"), row.get("rating"),
                    row.get("headshot_percentage"), row.get("first_kills"),
                    row.get("first_deaths"), row.get("clutches_won"),
                ),
            )
        conn.execute("UPDATE bdl_match_maps SET stats_synced = 1 WHERE id = ?", (match_map_id,))


def upsert_player_match_stats(match_id: int, rows: list[dict]) -> None:
    with _connect() as conn:
        for row in rows:
            player = row.get("player") or {}
            pid = player.get("id")
            if not pid:
                continue
            conn.execute(
                """
                INSERT INTO bdl_player_match_stats (
                    player_id, match_id, team_id, kills, deaths, assists, adr, kast,
                    rating, hs_pct, first_kills, first_deaths, clutches
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, match_id) DO UPDATE SET
                    kills=excluded.kills, deaths=excluded.deaths, team_id=excluded.team_id
                """,
                (
                    pid, match_id, row.get("team_id"),
                    row.get("kills"), row.get("deaths"), row.get("assists"),
                    row.get("adr"), row.get("kast"), row.get("rating"),
                    row.get("headshot_percentage"), row.get("first_kills"),
                    row.get("first_deaths"), row.get("clutches_won"),
                ),
            )
        conn.execute("UPDATE bdl_matches SET match_stats_synced = 1 WHERE id = ?", (match_id,))


def next_maps_job_ids(limit: int = 8) -> list[int]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id FROM bdl_matches
            WHERE status_state = 'final' AND IFNULL(maps_synced, 0) = 0
            ORDER BY start_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [int(r["id"]) for r in rows]


def next_map_stats_id() -> int | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM bdl_match_maps
            WHERE IFNULL(stats_synced, 0) = 0 AND map_name IS NOT NULL
            ORDER BY match_id DESC, map_number ASC
            LIMIT 1
            """
        ).fetchone()
    return int(row["id"]) if row else None


def match_map_count(match_ids: list[int]) -> int:
    if not match_ids:
        return 0
    placeholders = ",".join("?" * len(match_ids))
    with _connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM bdl_match_maps WHERE match_id IN ({placeholders})",
            match_ids,
        ).fetchone()
    return int(row["n"]) if row else 0


def recent_final_match_ids(team_id: int, limit: int = 8) -> list[int]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id FROM bdl_matches
            WHERE status_state = 'final' AND (team1_id = ? OR team2_id = ?)
            ORDER BY start_time DESC
            LIMIT ?
            """,
            (team_id, team_id, limit),
        ).fetchall()
    return [int(r["id"]) for r in rows]


def unsynced_map_ids_for_matches(match_ids: list[int], limit: int = 8) -> list[int]:
    if not match_ids:
        return []
    placeholders = ",".join("?" * len(match_ids))
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id FROM bdl_match_maps
            WHERE match_id IN ({placeholders})
              AND IFNULL(stats_synced, 0) = 0
              AND map_name IS NOT NULL
            ORDER BY match_id DESC, map_number ASC
            LIMIT ?
            """,
            (*match_ids, limit),
        ).fetchall()
    return [int(r["id"]) for r in rows]


def next_match_stats_ids_for_team(team_id: int, limit: int = 3) -> list[int]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id FROM bdl_matches
            WHERE status_state = 'final'
              AND (team1_id = ? OR team2_id = ?)
              AND IFNULL(match_stats_synced, 0) = 0
            ORDER BY start_time DESC
            LIMIT ?
            """,
            (team_id, team_id, limit),
        ).fetchall()
    return [int(r["id"]) for r in rows]


def next_match_stats_id() -> int | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM bdl_matches
            WHERE status_state = 'final' AND IFNULL(match_stats_synced, 0) = 0
              AND IFNULL(maps_synced, 0) = 1
            ORDER BY start_time DESC
            LIMIT 1
            """
        ).fetchone()
    return int(row["id"]) if row else None


def find_player(player_key: str) -> dict | None:
    key = (player_key or "").strip().lower()
    if not key:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM bdl_players
            WHERE player_key = ?
            ORDER BY is_active DESC, id DESC
            LIMIT 1
            """,
            (key,),
        ).fetchone()
        if not row:
            row = conn.execute(
                """
                SELECT * FROM bdl_players
                WHERE lower(nickname) = ?
                ORDER BY is_active DESC, id DESC
                LIMIT 1
                """,
                (key,),
            ).fetchone()
        if not row and len(key) >= 4:
            row = conn.execute(
                """
                SELECT * FROM bdl_players
                WHERE player_key LIKE ? OR lower(full_name) LIKE ?
                ORDER BY is_active DESC, length(nickname) ASC
                LIMIT 1
                """,
                (f"{key}%", f"%{key}%"),
            ).fetchone()
    return dict(row) if row else None


def mark_miss(player_key: str) -> None:
    if player_key:
        meta_set(f"miss:{player_key}", now())


def is_miss(player_key: str, hours: float = 24) -> bool:
    raw = meta_get(f"miss:{player_key}")
    if not raw:
        return False
    try:
        ts = datetime.fromisoformat(raw)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - ts).total_seconds() < hours * 3600


def clear_miss(player_key: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM bdl_meta WHERE k = ?", (f"miss:{player_key}",))


def want_hydrate(player_key: str) -> None:
    if player_key:
        meta_set(f"hydrate:{player_key}", "1")


def pop_hydrate(player_key: str) -> bool:
    flag = meta_get(f"hydrate:{player_key}")
    if flag:
        with _connect() as conn:
            conn.execute("DELETE FROM bdl_meta WHERE k = ?", (f"hydrate:{player_key}",))
    return bool(flag)


def search_players(query: str, limit: int = 30) -> list[dict]:
    q = f"%{(query or '').strip().lower()}%"
    with _connect() as conn:
        if query.strip():
            rows = conn.execute(
                """
                SELECT * FROM bdl_players
                WHERE player_key LIKE ? OR lower(nickname) LIKE ? OR lower(full_name) LIKE ?
                ORDER BY is_active DESC, nickname
                LIMIT ?
                """,
                (q, q, q, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM bdl_players
                WHERE is_active = 1
                ORDER BY nickname
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def find_team(team_name: str, team_key_fn) -> dict | None:
    key = team_key_fn(team_name)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM bdl_teams WHERE team_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        if not row and team_name:
            row = conn.execute(
                """
                SELECT * FROM bdl_teams
                WHERE lower(name) = ? OR lower(short_name) = ?
                LIMIT 1
                """,
                (team_name.lower(), team_name.lower()),
            ).fetchone()
    return dict(row) if row else None


def team_rank(team_id: int | None) -> dict | None:
    if not team_id:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM bdl_rankings WHERE team_id = ?", (team_id,)).fetchone()
    return dict(row) if row else None


def map_pool(team_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM bdl_map_pool
            WHERE team_id = ? AND IFNULL(matches_played, 0) >= 3
            ORDER BY win_rate DESC, matches_played DESC
            """,
            (team_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def player_map_rows(player_id: int, limit: int = 12) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, m.map_name, m.map_number, m.team1_score, m.team2_score,
                   g.start_time, g.best_of, g.team1_name, g.team2_name, g.team1_id, g.team2_id,
                   g.tournament, g.id AS match_id
            FROM bdl_player_map_stats s
            JOIN bdl_match_maps m ON m.id = s.match_map_id
            JOIN bdl_matches g ON g.id = m.match_id
            WHERE s.player_id = ?
            ORDER BY g.start_time DESC, m.map_number ASC
            LIMIT ?
            """,
            (player_id, limit * 3),
        ).fetchall()
    return [dict(r) for r in rows]


def player_match_rows(player_id: int, limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, g.start_time, g.best_of, g.team1_name, g.team2_name,
                   g.team1_id, g.team2_id, g.team1_score, g.team2_score, g.tournament
            FROM bdl_player_match_stats s
            JOIN bdl_matches g ON g.id = s.match_id
            WHERE s.player_id = ?
            ORDER BY g.start_time DESC
            LIMIT ?
            """,
            (player_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
