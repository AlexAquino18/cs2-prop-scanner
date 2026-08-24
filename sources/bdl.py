"""BallDontLie CS2 HTTP client. Rate-limited. Key from env only."""
from __future__ import annotations

import threading
import time

import requests

import config

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "cs2-prop-scanner/1.0",
}

_LOCK = threading.Lock()
_NEXT = 0.0
_session = requests.Session()


class BdlError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        super().__init__(f"BDL {status}: {body[:240]}")


def enabled() -> bool:
    return bool(config.BALLDONTLIE_API_KEY)


def _wait() -> None:
    global _NEXT
    gap = max(0.2, config.BALLDONTLIE_MIN_INTERVAL)
    with _LOCK:
        now = time.time()
        delay = _NEXT - now
        _NEXT = max(now, _NEXT) + gap
    if delay > 0:
        time.sleep(delay)


def get(path: str, params: list[tuple] | dict | None = None) -> dict:
    if not enabled():
        raise BdlError(401, "missing BALLDONTLIE_API_KEY")
    _wait()
    headers = {**HEADERS, "Authorization": config.BALLDONTLIE_API_KEY}
    url = f"{config.BALLDONTLIE_BASE_URL}{path}"
    resp = _session.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
    if resp.status_code == 429:
        time.sleep(20)
        _wait()
        resp = _session.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
    if resp.status_code >= 400:
        raise BdlError(resp.status_code, resp.text)
    payload = resp.json()
    return payload if isinstance(payload, dict) else {"data": payload}
