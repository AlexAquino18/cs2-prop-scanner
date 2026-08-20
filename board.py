"""Build dashboard payloads from stored snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import matching
import store
from models import PropLine

ET = ZoneInfo("America/New_York")


def parse_dt(starts_at: str | None) -> datetime | None:
    if not starts_at:
        return None
    text = starts_at.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def et_date(starts_at: str | None):
    dt = parse_dt(starts_at)
    return dt.date() if dt else None


def fmt_time(starts_at: str | None) -> str:
    dt = parse_dt(starts_at)
    if not dt:
        return ""
    return dt.strftime("%I:%M %p").lstrip("0")


def line_cell(current: float | None, previous: float | None, had_prev_snapshot: bool) -> dict:
    if current is None:
        return {"value": None, "delta": None, "text": "—", "dir": ""}
    text = f"{current:.1f}"
    if not had_prev_snapshot:
        return {"value": current, "delta": None, "text": text, "dir": ""}
    if previous is None:
        return {"value": current, "delta": None, "text": f"{text} new", "dir": "new"}
    delta = round(current - previous, 1)
    if abs(delta) < 0.05:
        return {"value": current, "delta": 0, "text": text, "dir": ""}
    sign = "+" if delta > 0 else ""
    return {
        "value": current,
        "delta": delta,
        "text": f"{text} ({sign}{delta:g})",
        "dir": "up" if delta > 0 else "down",
    }


def _index_lines(props: list[PropLine]) -> dict:
    out = {}
    for prop in props:
        key = (prop.player_key, prop.stat_key, prop.map_range or "full", prop.source)
        out.setdefault(key, prop)
    return out


def available_dates(props: list[PropLine]) -> list[str]:
    dates = {str(et_date(p.starts_at)) for p in props if et_date(p.starts_at)}
    return sorted(dates)


def pick_default_date(dates: list[str]) -> str | None:
    if not dates:
        return None
    today = datetime.now(ET).date().isoformat()
    upcoming = [d for d in dates if d >= today]
    return upcoming[0] if upcoming else dates[-1]


def build_dashboard(date: str | None = None, threshold: float = 0.5, limit: int = 50) -> dict:
    store.init_db()
    snaps = store.latest_snapshot_ids(2)
    if not snaps:
        return {
            "ok": False,
            "message": "No snapshot yet. Hit Refresh to pull PrizePicks and Underdog.",
            "dates": [],
            "date": date,
            "gaps": [],
            "movers": [],
            "stats": {},
        }

    latest_id, latest_at = snaps[0]
    prev_id = snaps[1][0] if len(snaps) > 1 else None
    latest_props = store.load_lines(latest_id)
    prev_props = store.load_lines(prev_id) if prev_id else []
    prev_index = _index_lines(prev_props)
    had_prev = prev_id is not None

    dates = available_dates(latest_props)
    chosen = date if date in dates else pick_default_date(dates)
    day_props = [p for p in latest_props if str(et_date(p.starts_at)) == chosen] if chosen else []

    groups = matching.group_props(day_props)
    discrepancies = matching.find_discrepancies(groups, threshold=threshold)

    gaps = []
    for disc in discrepancies[:limit]:
        any_prop = next(iter(disc.lines.values()))
        matchup = next((p.opponent for p in disc.lines.values() if p.opponent), "")
        pp = disc.lines.get("prizepicks")
        ud = disc.lines.get("underdog")
        pp_prev = (
            prev_index.get((pp.player_key, pp.stat_key, pp.map_range or "full", "prizepicks"))
            if pp else None
        )
        ud_prev = (
            prev_index.get((ud.player_key, ud.stat_key, ud.map_range or "full", "underdog"))
            if ud else None
        )
        prev_spread = None
        if pp_prev and ud_prev:
            prev_spread = round(abs(pp_prev.line - ud_prev.line), 1)
        spread_delta = None if prev_spread is None else round(disc.spread - prev_spread, 1)
        gaps.append(
            {
                "player": disc.player,
                "team": disc.team or "",
                "stat": disc.stat,
                "map": disc.map_range or "full",
                "matchup": matchup,
                "start": fmt_time(any_prop.starts_at),
                "spread": round(disc.spread, 1),
                "spread_delta": spread_delta,
                "prizepicks": line_cell(pp.line if pp else None, pp_prev.line if pp_prev else None, had_prev),
                "underdog": line_cell(ud.line if ud else None, ud_prev.line if ud_prev else None, had_prev),
            }
        )

    movers = []
    seen = set()
    for group in groups:
        if "prizepicks" not in group or "underdog" not in group:
            continue
        any_prop = next(iter(group.values()))
        key = (any_prop.player_key, any_prop.stat_key, any_prop.map_range or "full")
        if key in seen:
            continue
        seen.add(key)
        pp, ud = group["prizepicks"], group["underdog"]
        pp_prev = prev_index.get((*key, "prizepicks"))
        ud_prev = prev_index.get((*key, "underdog"))
        pp_delta = None if not pp_prev else round(pp.line - pp_prev.line, 1)
        ud_delta = None if not ud_prev else round(ud.line - ud_prev.line, 1)
        mag = max(abs(pp_delta or 0), abs(ud_delta or 0))
        if mag < 0.05:
            continue
        prev_spread = (
            round(abs(pp_prev.line - ud_prev.line), 1) if pp_prev and ud_prev else None
        )
        movers.append(
            {
                "player": any_prop.player_raw,
                "team": any_prop.team or "",
                "stat": any_prop.stat_key,
                "matchup": next((p.opponent for p in group.values() if p.opponent), ""),
                "max_move": mag,
                "spread_was": prev_spread,
                "spread_now": round(abs(pp.line - ud.line), 1),
                "prizepicks": line_cell(pp.line, pp_prev.line if pp_prev else None, had_prev),
                "underdog": line_cell(ud.line, ud_prev.line if ud_prev else None, had_prev),
            }
        )
    movers.sort(key=lambda m: (-m["max_move"], m["player"].lower()))
    movers = movers[:15]

    pp_n = sum(1 for p in day_props if p.source == "prizepicks")
    ud_n = sum(1 for p in day_props if p.source == "underdog")
    closed = sum(
        1 for m in movers if (m["spread_was"] or 0) >= 0.5 and m["spread_now"] < 0.05
    )

    return {
        "ok": True,
        "message": None,
        "date": chosen,
        "dates": dates,
        "threshold": threshold,
        "snapshot_at": latest_at,
        "stats": {
            "gaps": len(discrepancies),
            "movers": len(movers),
            "closed": closed,
            "prizepicks": pp_n,
            "underdog": ud_n,
            "max_spread": gaps[0]["spread"] if gaps else 0,
        },
        "gaps": gaps,
        "movers": movers,
    }
