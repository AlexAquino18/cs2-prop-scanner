"""Build dashboard payloads from stored snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config
import ingest
import matching
import store
from models import PropLine
from sources import polymarket
from teams import matchup_keys, split_matchup, team_key

BOOKS = config.BOOKS

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


def format_stat(stat_key: str, map_range: str | None) -> str:
    token = map_range or "full"
    if token == "full":
        return stat_key
    if "-" in token:
        return f"{stat_key} (maps {token})"
    return f"{stat_key} (map {token})"


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


def pick_default_date(dates: list[str], props: list[PropLine] | None = None) -> str | None:
    if not dates:
        return None
    today = datetime.now(ET).date().isoformat()
    upcoming = [d for d in dates if d >= today] or dates
    if not props:
        return upcoming[0]
    by_date: dict[str, list[PropLine]] = {}
    for prop in props:
        day = str(et_date(prop.starts_at) or "")
        if day in upcoming:
            by_date.setdefault(day, []).append(prop)

    def score(day: str) -> tuple:
        day_props = by_date.get(day) or []
        sources = len({p.source for p in day_props})
        return (sources, len(day_props))

    return max(upcoming, key=score)


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


def build_dashboard(date: str | None = None, threshold: float = 0.5, limit: int = 150) -> dict:
    store.init_db()
    snaps = store.latest_snapshot_ids(2)
    if not snaps:
        return {
            "ok": False,
            "message": "No snapshot yet. Hit Refresh to pull PrizePicks, Underdog, and Betr.",
            "dates": [],
            "date": date,
            "gaps": [],
            "movers": [],
            "matches": [],
            "stats": {},
            "source_counts": {},
            "source_errors": {},
        }

    latest_id, latest_at = snaps[0]
    latest_props = store.load_lines(latest_id)
    openings = store.load_openings()

    dates = available_dates(latest_props)
    chosen = date if date in dates else pick_default_date(dates, latest_props)
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
        book_cells = {}
        open_lines = []
        for src in BOOKS:
            prop = disc.lines.get(src)
            opening = None
            if prop:
                opening = openings.get(
                    (prop.player_key, prop.stat_key, prop.map_range or "full", src)
                )
                if opening:
                    open_lines.append(opening.line)
            book_cells[src] = line_cell(
                prop.line if prop else None,
                opening.line if opening else None,
            )
        open_spread = None
        if len(open_lines) >= 2:
            open_spread = round(max(open_lines) - min(open_lines), 1)
        spread_delta = None if open_spread is None else round(disc.spread - open_spread, 1)
        team = disc.team or ""
        series = _series_payload(polymarket.match_series(matchup_keys(matchup, team), series_odds))
        row = {
            "player": disc.player,
            "player_key": any_prop.player_key,
            "team": team,
            "stat": format_stat(disc.stat, disc.map_range),
            "stat_key": disc.stat,
            "map": disc.map_range or "full",
            "matchup": matchup,
            "start": fmt_time(any_prop.starts_at),
            "starts_at": any_prop.starts_at,
            "spread": round(disc.spread, 1),
            "spread_delta": spread_delta,
            "group": _group_key(matchup, team, any_prop.starts_at),
            "series": series,
        }
        row.update(book_cells)
        gaps.append(row)

    movers = []
    seen = set()
    for group in groups:
        if len(group) < 2:
            continue
        any_prop = next(iter(group.values()))
        map_range = matching.preferred_map_range(group)
        key = (any_prop.player_key, any_prop.stat_key, map_range or "full")
        if key in seen:
            continue
        seen.add(key)
        book_cells = {}
        deltas = []
        open_lines = []
        live_lines = []
        for src in BOOKS:
            prop = group.get(src)
            opening = openings.get((*key, src)) if prop else None
            book_cells[src] = line_cell(
                prop.line if prop else None,
                opening.line if opening else None,
            )
            if prop:
                live_lines.append(prop.line)
            if opening:
                open_lines.append(opening.line)
            if prop and opening:
                deltas.append(abs(round(prop.line - opening.line, 1)))
        mag = max(deltas) if deltas else 0
        if mag < 0.05:
            continue
        open_spread = (
            round(max(open_lines) - min(open_lines), 1) if len(open_lines) >= 2 else None
        )
        spread_now = (
            round(max(live_lines) - min(live_lines), 1) if len(live_lines) >= 2 else None
        )
        mover = {
            "player": any_prop.player_raw,
            "player_key": any_prop.player_key,
            "team": any_prop.team or "",
            "stat": format_stat(any_prop.stat_key, map_range),
            "stat_key": any_prop.stat_key,
            "map": map_range or "full",
            "matchup": next((p.opponent for p in group.values() if p.opponent), ""),
            "max_move": mag,
            "spread_was": open_spread,
            "spread_now": spread_now,
        }
        mover.update(book_cells)
        movers.append(mover)
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
        names = []
        for gap in bucket["gaps"]:
            team_name = (gap.get("team") or "").strip()
            if team_name and team_name not in names:
                names.append(team_name)
        bucket["sides"] = names[:2]
        bucket["label"] = _match_label(sample["matchup"], sample["team"], bucket["series"])
        for gap in bucket["gaps"]:
            gap.pop("group", None)
            gap.pop("starts_at", None)
            gap.pop("series", None)
        matches.append(bucket)
    matches.sort(key=lambda m: (m.get("starts_at") or "9999", -(m["max_spread"] or 0)))
    for match in matches:
        match.pop("starts_at", None)

    source_counts = {
        src: sum(1 for p in day_props if p.source == src) for src in BOOKS
    }
    closed = sum(
        1 for m in movers
        if (m["spread_was"] or 0) >= 0.5 and (m["spread_now"] or 0) < 0.05
    )
    ingest_state = {}
    try:
        ingest_state = ingest.status()
    except Exception:
        ingest_state = {}
    last_counts = ingest_state.get("last_counts") or {}
    source_errors = {
        name: text
        for name, text in last_counts.items()
        if name in BOOKS and isinstance(text, str) and text.startswith("error")
    }
    live_books = sum(1 for src in BOOKS if source_counts.get(src))
    message = None
    if source_errors:
        if set(source_errors) <= {"betr"} and live_books >= 2:
            message = (
                "Betr CS2 lines need a Betr login (BETR_ACCESS_TOKEN or "
                "BETR_USERNAME / BETR_PASSWORD). PrizePicks and Underdog are live."
            )
        else:
            bits = [f"{name}: {err}" for name, err in source_errors.items()]
            message = "Some books failed — " + " · ".join(bits)
    elif live_books < 2:
        message = (
            "Need lines from at least two books to show gaps. "
            f"PrizePicks {source_counts.get('prizepicks', 0)}, "
            f"Underdog {source_counts.get('underdog', 0)}, "
            f"Betr {source_counts.get('betr', 0)}."
        )

    return {
        "ok": True,
        "message": message,
        "date": chosen,
        "dates": dates,
        "threshold": threshold,
        "snapshot_at": latest_at,
        "stats": {
            "gaps": len(discrepancies),
            "movers": len(movers),
            "closed": closed,
            "prizepicks": source_counts.get("prizepicks", 0),
            "underdog": source_counts.get("underdog", 0),
            "betr": source_counts.get("betr", 0),
            "max_spread": gaps[0]["spread"] if gaps else 0,
            "matches": len(matches),
            "series": sum(1 for m in matches if m.get("series")),
        },
        "source_counts": source_counts,
        "source_errors": source_errors,
        "gaps": gaps,
        "movers": movers,
        "matches": matches,
    }
