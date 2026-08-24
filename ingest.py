"""Fetch live books and persist a snapshot."""
from __future__ import annotations

import threading

import requests

import store
from sources import chalkboard, prizepicks, underdog

_LOCK = threading.Lock()
_STATE = {
    "running": False,
    "error": None,
    "last_snapshot_id": None,
    "last_counts": {},
}


def status() -> dict:
    latest = store.latest_snapshot_ids(1)
    created = latest[0][1] if latest else None
    bdl = {}
    try:
        import bdl_sync

        bdl = bdl_sync.status()
    except Exception:
        bdl = {}
    return {
        "running": _STATE["running"],
        "error": _STATE["error"],
        "last_snapshot_id": _STATE["last_snapshot_id"] or (latest[0][0] if latest else None),
        "last_snapshot_at": created,
        "last_counts": _STATE["last_counts"],
        "has_data": bool(latest),
        "bdl": bdl,
    }


def run_ingest() -> dict:
    if not _LOCK.acquire(blocking=False):
        return status()
    _STATE["running"] = True
    _STATE["error"] = None
    try:
        store.init_db()
        session = requests.Session()
        counts = {}
        props = []
        for name, fetcher in (
            ("prizepicks", prizepicks.fetch),
            ("underdog", underdog.fetch),
            ("chalkboard", chalkboard.fetch),
        ):
            try:
                batch = fetcher(session)
            except Exception as exc:
                counts[name] = f"error: {exc}"
                continue
            counts[name] = len(batch)
            props.extend(batch)
        snapshot_id = store.save_snapshot(props)
        try:
            import bdl_sync

            names = list({p.player_raw for p in props if p.player_raw})
            teams = list({p.team for p in props if p.team})
            bdl_sync.schedule_board(names, teams)
            counts["bdl"] = bdl_sync.status()
        except Exception as exc:
            counts["bdl"] = f"error: {exc}"
        try:
            from sources import polymarket

            counts["polymarket"] = len(polymarket.get_series_odds(session))
        except Exception as exc:
            counts["polymarket"] = f"error: {exc}"
        _STATE["last_snapshot_id"] = snapshot_id
        _STATE["last_counts"] = counts
        _STATE["running"] = False
        return status()
    except Exception as exc:
        _STATE["error"] = str(exc)
        raise
    finally:
        _STATE["running"] = False
        _LOCK.release()
