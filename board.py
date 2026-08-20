"""Build dashboard payloads from stored snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import matching
import store
from models import PropLine
from sources import polymarket
from teams import matchup_keys, split_matchup, team_key

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


def _series_payload(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "label": row["label"],
        "a": row["a"],
        "b": row["b"],
        "url": row["url"],
    }


def _group_key(matchup: str, team: str, starts_at: str | None) -> tuple:
    sides = [team_key(part) for part in split_matchup(matchup)]
    sides = [key for key in sides if key]
    if len(sides) >= 2:
        a, b = sorted(sides[:2])
        return ("pair", a, b)
    if sides:
        hour = (parse_dt(starts_at) or datetime.now(ET)).strftime("%Y-%m-%d %H")
        return ("team", sides[0], hour)
    tk = team_key(team)
    if tk:
        hour = (parse_dt(starts_at) or datetime.now(ET)).strftime("%Y-%m-%d %H")
        return ("team", tk, hour)
    return ("other", matchup or team or "unknown", str(et_date(starts_at) or ""))


def _match_label(matchup: str, team: str, series: dict | None) -> str:
    if series and series.get("label"):
        return series["label"]
    if matchup:
        return matchup
    return team or "Other"


def build_dashboard(date: str | None = None, threshold: float = 0.5, limit: int = 80) -> dict:
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
            "matches": [],
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
    try:
        series_odds = polymarket.get_series_odds()
    except Exception:
        series_odds = []

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
        team = disc.team or ""
        series = _series_payload(polymarket.match_series(matchup_keys(matchup, team), series_odds))
        gaps.append(
            {
                "player": disc.player,
                "team": team,
                "stat": disc.stat,
                "map": disc.map_range or "full",
                "matchup": matchup,
                "start": fmt_time(any_prop.starts_at),
                "starts_at": any_prop.starts_at,
                "spread": round(disc.spread, 1),
                "spread_delta": spread_delta,
                "group": _group_key(matchup, team, any_prop.starts_at),
                "series": series,
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

    buckets: dict[tuple, dict] = {}
    for gap in gaps:
        bucket = buckets.setdefault(
            gap["group"],
            {
                "label": "",
                "start": gap["start"],
                "starts_at": gap["starts_at"],
                "series": gap["series"],
                "max_spread": 0,
                "gaps": [],
            },
        )
        bucket["gaps"].append(gap)
        bucket["max_spread"] = max(bucket["max_spread"], gap["spread"])
        if gap["series"] and not bucket["series"]:
            bucket["series"] = gap["series"]
        if gap["starts_at"] and (
            not bucket["starts_at"] or gap["starts_at"] < bucket["starts_at"]
        ):
            bucket["start"] = gap["start"]
            bucket["starts_at"] = gap["starts_at"]
    matches = []
    for key, bucket in buckets.items():
        sample = bucket["gaps"][0]
        bucket["label"] = _match_label(sample["matchup"], sample["team"], bucket["series"])
        for gap in bucket["gaps"]:
            gap.pop("group", None)
            gap.pop("starts_at", None)
            gap.pop("series", None)
        matches.append(bucket)
    matches.sort(key=lambda m: (m.get("starts_at") or "9999", -(m["max_spread"] or 0)))
    for match in matches:
        match.pop("starts_at", None)

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
            "matches": len(matches),
            "series": sum(1 for m in matches if m.get("series")),
        },
        "gaps": gaps,
        "movers": movers,
        "matches": matches,
    }
