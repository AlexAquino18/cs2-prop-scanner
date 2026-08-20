"""SQLite snapshot store for CS2 prop lines."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models import PropLine

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "props.db"
KEEP_SNAPSHOTS = 48


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                player_raw TEXT NOT NULL,
                player_key TEXT NOT NULL,
                team TEXT,
                stat_raw TEXT,
                stat_key TEXT NOT NULL,
                map_range TEXT,
                line REAL NOT NULL,
                starts_at TEXT,
                opponent TEXT,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_lines_snapshot
                ON lines(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_lines_match
                ON lines(snapshot_id, player_key, stat_key, map_range, source);
            CREATE TABLE IF NOT EXISTS openings (
                player_key TEXT NOT NULL,
                stat_key TEXT NOT NULL,
                map_range TEXT NOT NULL,
                source TEXT NOT NULL,
                player_raw TEXT NOT NULL,
                team TEXT,
                opponent TEXT,
                line REAL NOT NULL,
                starts_at TEXT,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (player_key, stat_key, map_range, source)
            );
            """
        )


def save_snapshot(props: list[PropLine]) -> int:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO snapshots (created_at) VALUES (?)", (created_at,)
        )
        snapshot_id = int(cur.lastrowid)
        conn.executemany(
            """
            INSERT INTO lines (
                snapshot_id, source, player_raw, player_key, team,
                stat_raw, stat_key, map_range, line, starts_at, opponent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_id,
                    p.source,
                    p.player_raw,
                    p.player_key,
                    p.team,
                    p.stat_raw,
                    p.stat_key,
                    p.map_range,
                    p.line,
                    p.starts_at,
                    p.opponent,
                )
                for p in props
            ],
        )
        old = conn.execute(
            """
            SELECT id FROM snapshots
            ORDER BY id DESC
            LIMIT -1 OFFSET ?
            """,
            (KEEP_SNAPSHOTS,),
        ).fetchall()
        if old:
            ids = [row["id"] for row in old]
            conn.executemany("DELETE FROM snapshots WHERE id = ?", [(i,) for i in ids])
            conn.executemany("DELETE FROM lines WHERE snapshot_id = ?", [(i,) for i in ids])
        _sync_openings(conn, props, created_at)
        return snapshot_id


def latest_snapshot_ids(n: int = 2) -> list[tuple[int, str]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at FROM snapshots ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
    return [(int(r["id"]), r["created_at"]) for r in rows]


def load_lines(snapshot_id: int) -> list[PropLine]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM lines WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
    return [
        PropLine(
            source=r["source"],
            player_raw=r["player_raw"],
            player_key=r["player_key"],
            team=r["team"],
            stat_raw=r["stat_raw"] or "",
            stat_key=r["stat_key"],
            map_range=r["map_range"],
            line=float(r["line"]),
            opponent=r["opponent"],
            starts_at=r["starts_at"],
        )
        for r in rows
    ]


def _opening_key(player_key, stat_key, map_range, source) -> tuple:
    return (player_key, stat_key, map_range or "full", source)


def _backfill_openings(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) AS n FROM openings").fetchone()["n"]
    if count:
        return
    snaps = conn.execute("SELECT id, created_at FROM snapshots ORDER BY id ASC").fetchall()
    seen = set()
    for snap in snaps:
        rows = conn.execute(
            "SELECT * FROM lines WHERE snapshot_id = ?",
            (snap["id"],),
        ).fetchall()
        for r in rows:
            key = _opening_key(r["player_key"], r["stat_key"], r["map_range"], r["source"])
            if key in seen:
                continue
            seen.add(key)
            conn.execute(
                """
                INSERT INTO openings (
                    player_key, stat_key, map_range, source, player_raw,
                    team, opponent, line, starts_at, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key[0], key[1], key[2], key[3],
                    r["player_raw"], r["team"], r["opponent"],
                    r["line"], r["starts_at"], snap["created_at"],
                ),
            )


def _sync_openings(conn: sqlite3.Connection, props: list[PropLine], seen_at: str) -> None:
    _backfill_openings(conn)
    live_by_source: dict[str, set[tuple]] = {}
    for p in props:
        key = _opening_key(p.player_key, p.stat_key, p.map_range, p.source)
        live_by_source.setdefault(p.source, set()).add(key)
        conn.execute(
            """
            INSERT OR IGNORE INTO openings (
                player_key, stat_key, map_range, source, player_raw,
                team, opponent, line, starts_at, first_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key[0], key[1], key[2], key[3],
                p.player_raw, p.team, p.opponent, p.line, p.starts_at, seen_at,
            ),
        )
    existing = conn.execute(
        "SELECT player_key, stat_key, map_range, source FROM openings"
    ).fetchall()
    for row in existing:
        key = (row["player_key"], row["stat_key"], row["map_range"], row["source"])
        source = key[3]
        # A book with zero lines this pull is treated as a fetch miss, not a wipe.
        live = live_by_source.get(source)
        if live is None or key in live:
            continue
        conn.execute(
            """
            DELETE FROM openings
            WHERE player_key = ? AND stat_key = ? AND map_range = ? AND source = ?
            """,
            key,
        )


def load_openings() -> dict[tuple, PropLine]:
    with _connect() as conn:
        _backfill_openings(conn)
        rows = conn.execute("SELECT * FROM openings").fetchall()
    out = {}
    for r in rows:
        key = _opening_key(r["player_key"], r["stat_key"], r["map_range"], r["source"])
        out[key] = PropLine(
            source=r["source"],
            player_raw=r["player_raw"],
            player_key=r["player_key"],
            team=r["team"],
            stat_raw="",
            stat_key=r["stat_key"],
            map_range=None if r["map_range"] == "full" else r["map_range"],
            line=float(r["line"]),
            opponent=r["opponent"],
            starts_at=r["starts_at"],
        )
    return out
