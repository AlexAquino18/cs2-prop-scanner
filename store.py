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
