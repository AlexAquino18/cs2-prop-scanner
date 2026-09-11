"""
Betr Picks CS2 adapter.

Betr labels the league CSGO in GraphQL and CS2 in the lobby.
Lobby projections need the Fantasy-* headers the web app sends.
Some queries (league configs) are public; upcoming events usually
need a Keycloak access token from a Betr account.
"""
from __future__ import annotations

import requests

import config
from models import PropLine
from normalize import normalize_player_name, normalize_stat

BETR_GRAPHQL_URL = "https://api.fantasy.betr.app/graphql"
BETR_TOKEN_URL = (
    "https://account.betr.app/realms/betr/protocol/openid-connect/token"
)
BETR_CLIENT_ID = "betr-rn"
LIVE_STATUSES = {"live", "in_progress", "inplay", "started"}
CLOSED_STATUSES = {
    "closed",
    "completed",
    "complete",
    "settled",
    "final",
    "cancelled",
    "canceled",
    "suspended",
    "inactive",
}

CS2_QUERY = """
fragment EventInfoData on EventV2 {
    id
    date
    status
    sport
    league
    competitionType
    name
    icon
    dedicated
}
fragment TeamInfo on Team {
    id
    name
    league
    sport
    icon
    fullName
}
fragment PlayerInfo on Player {
    id
    firstName
    lastName
    icon
    position
    jerseyNumber
}
fragment PlayerProjection on Projection {
    marketId
    marketStatus
    isLive
    type
    label
    name
    key
    order
    value
    currentValue
    liveScoringDisabled
}
fragment PlayerInfoWithProjections on Player {
    ...PlayerInfo
    projections { ...PlayerProjection }
}
fragment TeamInfoWithPlayers on Team {
    ...TeamInfo
    players { ...PlayerInfoWithProjections }
}
query LeagueUpcomingEvents($league: League!) {
    getUpcomingEventsV2(league: $league) {
        ...EventInfoData
        ... on TeamTournamentEvent {
            teams { ...TeamInfoWithPlayers }
        }
        ... on TeamVersusEvent {
            teams { ...TeamInfoWithPlayers }
        }
        ... on IndividualTournamentEvent {
            players { ...PlayerInfoWithProjections }
        }
        ... on IndividualVersusEvent {
            players { ...PlayerInfoWithProjections }
        }
    }
}
"""


def _headers(token: str | None = None) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://picks.betr.app",
        "Referer": "https://picks.betr.app/",
        "Fantasy-Api-Version": config.BETR_API_VERSION,
        "Fantasy-Application-Version": config.BETR_APP_VERSION,
        "Promotions-Api-Version": config.BETR_PROMOTIONS_API_VERSION,
        "Channel": "WEB",
    }
    if token:
        headers["Authorization"] = (
            token if token.lower().startswith("bearer ") else token
        )
    return headers


def _access_token(session: requests.Session) -> str | None:
    if config.BETR_ACCESS_TOKEN:
        return config.BETR_ACCESS_TOKEN
    user = config.BETR_USERNAME
    password = config.BETR_PASSWORD
    if not user or not password:
        return None
    resp = session.post(
        BETR_TOKEN_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        },
        data={
            "grant_type": "password",
            "client_id": BETR_CLIENT_ID,
            "username": user,
            "password": password,
            "scope": "openid profile email offline_access",
        },
        timeout=config.REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    token = (resp.json() or {}).get("access_token")
    if not token:
        raise RuntimeError("Betr login did not return an access token")
    return str(token)


def _player_name(player: dict) -> str:
    first = (player.get("firstName") or "").strip()
    last = (player.get("lastName") or "").strip()
    return " ".join(part for part in (first, last) if part)


def _stat_label(projection: dict) -> str:
    parts = [
        projection.get("label"),
        projection.get("name"),
        projection.get("key"),
        projection.get("type"),
    ]
    return " ".join(str(part) for part in parts if part)


def _closed(status) -> bool:
    return str(status or "").strip().lower() in CLOSED_STATUSES


def _live(status) -> bool:
    return str(status or "").strip().lower() in LIVE_STATUSES


def _iter_players(event: dict):
    teams = event.get("teams") or []
    names = [
        (team.get("name") or team.get("fullName") or "").strip()
        for team in teams
        if (team.get("name") or team.get("fullName"))
    ]
    matchup = " vs ".join(names) if len(names) >= 2 else (event.get("name") or "")
    for team in teams:
        team_name = (team.get("name") or team.get("fullName") or "").strip() or None
        for player in team.get("players") or []:
            yield player, team_name, matchup
    for player in event.get("players") or []:
        yield player, None, matchup or (event.get("name") or "")


def _parse_events(events: list) -> list[PropLine]:
    props: list[PropLine] = []
    for event in events or []:
        if _live(event.get("status")) or _closed(event.get("status")):
            continue
        starts_at = event.get("date")
        for player, team_name, matchup in _iter_players(event):
            player_name = _player_name(player)
            if not player_name:
                continue
            for projection in player.get("projections") or []:
                if projection.get("isLive") or _live(projection.get("marketStatus")):
                    continue
                if _closed(projection.get("marketStatus")):
                    continue
                line_value = projection.get("value")
                if line_value is None:
                    line_value = projection.get("currentValue")
                try:
                    line = float(line_value)
                except (TypeError, ValueError):
                    continue
                stat_label = _stat_label(projection)
                if not stat_label:
                    continue
                stat_key, map_range = normalize_stat(stat_label)
                props.append(
                    PropLine(
                        source="betr",
                        player_raw=player_name,
                        player_key=normalize_player_name(player_name),
                        team=team_name,
                        stat_raw=stat_label,
                        stat_key=stat_key,
                        map_range=map_range,
                        line=line,
                        opponent=matchup or None,
                        starts_at=starts_at,
                        extra={
                            "league": event.get("league"),
                            "event_id": event.get("id"),
                            "market_id": projection.get("marketId"),
                        },
                    )
                )
    return props


def _post_graphql(session: requests.Session, token: str | None, league: str) -> dict:
    body = {
        "operationName": "LeagueUpcomingEvents",
        "query": CS2_QUERY,
        "variables": {"league": league},
    }
    headers_list = [_headers(token)]
    if token and not token.lower().startswith("bearer "):
        extra = _headers(token)
        extra["Authorization"] = f"Bearer {token}"
        headers_list.append(extra)
    last_error = None
    for headers in headers_list:
        resp = session.post(
            BETR_GRAPHQL_URL,
            headers=headers,
            json=body,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        if resp.status_code == 401:
            last_error = RuntimeError(
                "Betr CS2 lobby needs a login. Set BETR_ACCESS_TOKEN "
                "or BETR_USERNAME and BETR_PASSWORD in .env"
            )
            continue
        resp.raise_for_status()
        payload = resp.json()
        errors = payload.get("errors") or []
        if errors:
            messages = "; ".join(
                str(err.get("message") or err) for err in errors[:3]
            )
            raise RuntimeError(f"Betr GraphQL error: {messages}")
        return payload
    if last_error:
        raise last_error
    raise RuntimeError("Betr CS2 fetch failed")


def fetch(session: requests.Session = None) -> list[PropLine]:
    session = session or requests.Session()
    token = _access_token(session)
    payload = _post_graphql(session, token, config.BETR_CS2_LEAGUE)
    events = (payload.get("data") or {}).get("getUpcomingEventsV2") or []
    return _parse_events(events)
