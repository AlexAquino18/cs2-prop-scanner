"""
PrizePicks CS2 adapter.

Uses PrizePicks' public partner API (no auth required).
"""
import requests

import config
from models import PropLine
from normalize import normalize_player_name, normalize_stat

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def fetch(session: requests.Session = None) -> list[PropLine]:
    session = session or requests.Session()
    props: list[PropLine] = []
    page = 1

    while True:
        params = {
            "league_id": config.PRIZEPICKS_CS2_LEAGUE_ID,
            "per_page": config.PRIZEPICKS_PER_PAGE,
            "page": page,
            "single_stat": "true",
        }
        resp = session.get(
            config.PRIZEPICKS_BASE_URL,
            params=params,
            headers=HEADERS,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()

        data = payload.get("data", [])
        if not data:
            break

        included = {
            (item["type"], item["id"]): item
            for item in payload.get("included", [])
        }

        for item in data:
            attrs = item.get("attributes", {})
            if attrs.get("is_live") or attrs.get("in_game"):
                continue
            if attrs.get("event_type") == "combo":
                continue
            odds_type = (attrs.get("odds_type") or "standard").lower()
            if odds_type != "standard":
                continue

            stat_label = attrs.get("stat_type") or attrs.get("stat_display_name") or ""
            if "combo" in stat_label.lower():
                continue
            line = attrs.get("line_score")
            if line is None:
                continue

            player_rel = (
                item.get("relationships", {})
                .get("new_player", {})
                .get("data")
            )
            player_attrs = {}
            if player_rel:
                player_item = included.get((player_rel["type"], player_rel["id"]))
                if player_item:
                    player_attrs = player_item.get("attributes", {})

            player_name = player_attrs.get("name") or attrs.get("description", "")
            if not player_name:
                continue
            team = player_attrs.get("team") or player_attrs.get("team_name")

            stat_key, map_range = normalize_stat(stat_label)
            if map_range is None:
                extra_label = " ".join(
                    part for part in (
                        attrs.get("stat_display_name"),
                        attrs.get("description"),
                    ) if part
                )
                if extra_label:
                    _, map_range = normalize_stat(extra_label)

            props.append(
                PropLine(
                    source="prizepicks",
                    player_raw=player_name,
                    player_key=normalize_player_name(player_name),
                    team=team,
                    stat_raw=stat_label,
                    stat_key=stat_key,
                    map_range=map_range,
                    line=float(line),
                    multiplier=attrs.get("odds_type"),
                    starts_at=attrs.get("start_time"),
                    extra={"id": item.get("id")},
                )
            )

        meta = payload.get("meta", {})
        total_pages = meta.get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    return props
