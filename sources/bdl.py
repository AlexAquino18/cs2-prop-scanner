"""BallDontLie CS2 HTTP client. Burst remaining quota, then wait for reset."""
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
_REMAINING = 5
_RESET = 0.0
_LIMIT = 5
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


def time_until_slot() -> float:
    with _LOCK:
        now = time.time()
        if _REMAINING > 0:
            return max(0.0, _NEXT - now)
        if _RESET > now:
            return _RESET - now
        return max(0.0, _NEXT - now)


def peek_delay() -> float:
    return time_until_slot()


def _wait() -> None:
    global _NEXT, _REMAINING
    gap = max(0.05, config.BALLDONTLIE_MIN_INTERVAL)
    with _LOCK:
        now = time.time()
        if _REMAINING <= 0 and _RESET > now:
            delay = _RESET - now
        else:
            delay = max(0.0, _NEXT - now)
        _NEXT = now + delay + gap
        if _REMAINING > 0:
            _REMAINING -= 1
    if delay > 0:
        time.sleep(delay)


def _read_limits(resp: requests.Response) -> None:
    global _REMAINING, _RESET, _LIMIT
    rem = resp.headers.get("X-RateLimit-Remaining")
    rst = resp.headers.get("X-RateLimit-Reset")
    lim = resp.headers.get("X-RateLimit-Limit")
    retry = resp.headers.get("Retry-After")
    with _LOCK:
        if lim:
            try:
                _LIMIT = max(1, int(float(lim)))
            except ValueError:
                pass
        if rem is not None:
            try:
                _REMAINING = max(0, int(float(rem)))
            except ValueError:
                pass
        if rst:
            try:
                val = float(rst)
                _RESET = val / 1000.0 if val > 1e12 else val
            except ValueError:
                pass
        elif retry:
            try:
                _RESET = time.time() + float(retry)
                _REMAINING = 0
            except ValueError:
                pass


def get(path: str, params: list[tuple] | dict | None = None, wait_budget: float | None = None) -> dict:
    if not enabled():
        raise BdlError(401, "missing BALLDONTLIE_API_KEY")
    delay = time_until_slot()
    if wait_budget is not None and delay > wait_budget:
        raise BdlBusy(delay)
    _wait()
    headers = {**HEADERS, "Authorization": config.BALLDONTLIE_API_KEY}
    url = f"{config.BALLDONTLIE_BASE_URL}{path}"
    resp = _session.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
    _read_limits(resp)
    if resp.status_code == 429:
        if wait_budget is not None:
            raise BdlError(429, resp.text)
        wait = time_until_slot()
        if wait <= 0:
            wait = 12
        time.sleep(min(65.0, wait + 0.2))
        _wait()
        resp = _session.get(url, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
        _read_limits(resp)
    if resp.status_code >= 400:
        raise BdlError(resp.status_code, resp.text)
    payload = resp.json()
    return payload if isinstance(payload, dict) else {"data": payload}
