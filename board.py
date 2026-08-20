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


def line_cell(current: float | None, opening: float | None) -> dict:
    if current is None:
        return {"value": None, "delta": None, "text": "—", "dir": ""}
    text = f"{current:.1f}"
    if opening is None:
        return {"value": current, "delta": None, "text": f"{text} new", "dir": "new"}
    delta = round(current - opening, 1)
    if abs(delta) < 0.05:
        return {"value": current, "delta": 0, "text": text, "dir": ""}
    sign = "+" if delta > 0 else ""
    return {
        "value": current,
        "delta": delta,
        "text": f"{text} ({sign}{delta:g} from {opening:.1f})",
        "dir": "up" if delta > 0 else "down",
    }


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
    latest_props = store.load_lines(latest_id)
    openings = store.load_openings()

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
        pp_open = (
            openings.get((pp.player_key, pp.stat_key, pp.map_range or "full", "prizepicks"))
            if pp else None
        )
        ud_open = (
            openings.get((ud.player_key, ud.stat_key, ud.map_range or "full", "underdog"))
            if ud else None
        )
        open_spread = None
        if pp_open and ud_open:
            open_spread = round(abs(pp_open.line - ud_open.line), 1)
        spread_delta = None if open_spread is None else round(disc.spread - open_spread, 1)
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
                "prizepicks": line_cell(pp.line if pp else None, pp_open.line if pp_open else None),
                "underdog": line_cell(ud.line if ud else None, ud_open.line if ud_open else None),
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
        pp_open = openings.get((*key, "prizepicks"))
        ud_open = openings.get((*key, "underdog"))
        pp_delta = None if not pp_open else round(pp.line - pp_open.line, 1)
        ud_delta = None if not ud_open else round(ud.line - ud_open.line, 1)
        mag = max(abs(pp_delta or 0), abs(ud_delta or 0))
        if mag < 0.05:
            continue
        open_spread = (
            round(abs(pp_open.line - ud_open.line), 1) if pp_open and ud_open else None
        )
        movers.append(
            {
                "player": any_prop.player_raw,
                "team": any_prop.team or "",
                "stat": any_prop.stat_key,
                "matchup": next((p.opponent for p in group.values() if p.opponent), ""),
                "max_move": mag,
                "spread_was": open_spread,
                "spread_now": round(abs(pp.line - ud.line), 1),
                "prizepicks": line_cell(pp.line, pp_open.line if pp_open else None),
                "underdog": line_cell(ud.line, ud_open.line if ud_open else None),
            }
        )
    movers.sort(key=lambda m: (-m["max_move"], m["player"].lower()))
    movers = movers[:40]

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
