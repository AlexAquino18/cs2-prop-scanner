"""Polymarket CS2 series-winner (match moneyline) odds. No API key."""
from __future__ import annotations

import json
import re
import threading
import time

import requests

import config
from teams import team_key

HEADERS = {
    "User-Agent": "cs2-prop-scanner/1.0",
    "Accept": "application/json",
}

_CACHE: dict = {"at": 0.0, "rows": []}
_LOCK = threading.Lock()
_TITLE_TEAMS = re.compile(
    r"Counter-Strike:\s*(.+?)\s*\((?:BO\d|bo\d)\)",
    re.I,
)


def _parse_json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _extract_label(title: str) -> str:
    if not title:
        return ""
    m = _TITLE_TEAMS.search(title)
    if m:
        return m.group(1).strip()
    return title.replace("Counter-Strike:", "").split(" - ")[0].strip()


def _load_events(session: requests.Session) -> list[dict]:
    seen = {}
    for params in (
        {"order": "id", "ascending": "false"},
        {"order": "volume24hr", "ascending": "false"},
    ):
        offset = 0
        while offset < 150:
            resp = session.get(
                f"{config.POLYMARKET_GAMMA_URL}/events",
                params={
                    "series_id": config.POLYMARKET_CS2_SERIES_ID,
                    "active": "true",
                    "closed": "false",
                    "limit": 50,
                    "offset": offset,
                    **params,
                },
                headers=HEADERS,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            for event in batch:
                slug = event.get("slug") or event.get("id")
                if slug:
                    seen[slug] = event
            if len(batch) < 50:
                break
            offset += 50
            if params["order"] == "volume24hr":
                break
    return list(seen.values())


def fetch_series_odds(session: requests.Session | None = None) -> list[dict]:
    session = session or requests.Session()
    events = _load_events(session)

    rows = []
    for event in events:
        markets = event.get("markets") or []
        moneyline = None
        for market in markets:
            if market.get("sportsMarketType") != "moneyline":
                continue
            title = (market.get("groupItemTitle") or "").lower()
            if title and title not in {"match winner", "moneyline", "winner"}:
                continue
            moneyline = market
            break
        if not moneyline or moneyline.get("closed"):
            continue
        names = [str(n) for n in _parse_json_list(moneyline.get("outcomes"))]
        prices = _parse_json_list(moneyline.get("outcomePrices"))
        if len(names) != 2:
            continue
        pcts = []
        for raw in prices[:2]:
            try:
                pcts.append(float(raw))
            except (TypeError, ValueError):
                pcts.append(None)
        if len(pcts) != 2 or None in pcts:
            continue
        if max(pcts) >= 0.98:
            continue
        a_pct = round(pcts[0] * 100)
        b_pct = round(pcts[1] * 100)
        slug = event.get("slug") or moneyline.get("slug") or ""
        label = _extract_label(event.get("title") or "")
        rows.append(
            {
                "label": label or f"{names[0]} vs {names[1]}",
                "a": {"name": names[0], "key": team_key(names[0]), "pct": a_pct},
                "b": {"name": names[1], "key": team_key(names[1]), "pct": b_pct},
                "url": f"https://polymarket.com/event/{slug}" if slug else "",
                "start": event.get("endDate") or event.get("startDate"),
            }
        )
    return rows


def get_series_odds(session: requests.Session | None = None) -> list[dict]:
    now = time.time()
    with _LOCK:
        if _CACHE["rows"] and now - _CACHE["at"] < config.POLYMARKET_CACHE_SECONDS:
            return _CACHE["rows"]
    try:
        rows = fetch_series_odds(session)
    except Exception:
        with _LOCK:
            return list(_CACHE["rows"])
    with _LOCK:
        _CACHE["at"] = now
        _CACHE["rows"] = rows
    return rows


def match_series(keys: set[str], odds: list[dict]) -> dict | None:
    if not keys or not odds:
        return None
    for row in odds:
        event_keys = {row["a"]["key"], row["b"]["key"]}
        event_keys.discard("")
        if len(event_keys) < 2:
            continue
        if event_keys <= keys or (len(keys) >= 2 and keys <= event_keys):
            return row
    if len(keys) == 1:
        hits = [
            row
            for row in odds
            if keys & {row["a"]["key"], row["b"]["key"]}
        ]
        if len(hits) == 1:
            return hits[0]
    return None
