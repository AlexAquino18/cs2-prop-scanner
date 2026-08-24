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


class BdlBusy(Exception):
    def __init__(self, delay: float):
        self.delay = delay
        super().__init__(f"rate-limited for {delay:.0f}s")


def enabled() -> bool:
    return bool(config.BALLDONTLIE_API_KEY)


def peek_delay() -> float:
    with _LOCK:
        return max(0.0, _NEXT - time.time())


def _wait() -> None:
    global _NEXT
    gap = max(0.2, config.BALLDONTLIE_MIN_INTERVAL)
    with _LOCK:
        now = time.time()
        delay = _NEXT - now
        _NEXT = max(now, _NEXT) + gap
    if delay > 0:
        time.sleep(delay)


def get(path: str, params: list[tuple] | dict | None = None, wait_budget: float | None = None) -> dict:
    if not enabled():
        raise BdlError(401, "missing BALLDONTLIE_API_KEY")
    if wait_budget is not None and peek_delay() > wait_budget:
        raise BdlBusy(peek_delay())
    _wait()
    headers = {**HEADERS, "Authorization": config.BALLDONTLIE_API_KEY}
    url = f"{config.BALLDONTLIE_BASE_URL}{path}"
    resp = _session.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
    if resp.status_code == 429:
        if wait_budget is not None:
            raise BdlError(429, resp.text)
        time.sleep(60)
        _wait()
        resp = _session.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
    if resp.status_code >= 400:
        raise BdlError(resp.status_code, resp.text)
    payload = resp.json()
    return payload if isinstance(payload, dict) else {"data": payload}
