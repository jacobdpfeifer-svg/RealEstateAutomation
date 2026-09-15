"""Conservative HTTP policy for this sequential batch job (no POST replay)."""
from __future__ import annotations

import os
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests


class SourceChangedError(RuntimeError):
    """A source no longer satisfies the adapter's expected data contract."""


class SourceBlockedError(RuntimeError):
    """Access denied or a server cooldown too long for this run."""


def safe_error(exc: Exception) -> str:
    """Never persist exception text: request URLs and bodies can contain PII."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return type(exc).__name__ + (f" HTTP {status}" if isinstance(status, int) else "")


class SourceSession(requests.Session):
    # Shared across instances: constructing an adapter per lead must not reset pacing.
    _last_request: dict[str, float] = {}
    _blocked_hosts: set[str] = set()
    retry_statuses = {429, 500, 502, 503, 504}

    def __init__(self, min_interval: float | None = None) -> None:
        super().__init__()
        self.min_interval = float(
            os.environ.get("LEADS_HTTP_INTERVAL", "1") if min_interval is None else min_interval
        )
        if not 0 <= self.min_interval <= 60:
            raise ValueError("LEADS_HTTP_INTERVAL must be between 0 and 60 seconds")
        self.headers["User-Agent"] = os.environ.get("LEADS_USER_AGENT", "re-tax-leads/0.1")

    def send(self, request, **kwargs):
        host = urlsplit(request.url).netloc
        if host in self._blocked_hosts:
            raise SourceBlockedError("Source blocked for this process; inspect access policy")
        delay = self.min_interval - (time.monotonic() - self._last_request.get(host, float("-inf")))
        if delay > 0:
            time.sleep(delay)
        self._last_request[host] = time.monotonic()
        response = super().send(request, **kwargs)
        if response.status_code in (401, 403):
            self._blocked_hosts.add(host)
        return response

    @staticmethod
    def _retry_delay(response, attempt: int) -> float:
        value = response.headers.get("Retry-After") if response is not None else None
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    when = parsedate_to_datetime(value)
                    delay = (when - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = 0
            # Stop instead of capping Retry-After and retrying before permission.
            if delay > 60:
                raise SourceBlockedError("Retry-After exceeds this run's wait budget")
            return max(0, delay)
        return 2 ** attempt + random.uniform(0, 0.5)

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (10, 60))
        attempts = 3 if method.upper() in {"GET", "HEAD"} else 1
        for attempt in range(attempts):
            response = None
            try:
                response = super().request(method, url, **kwargs)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == attempts - 1:
                    raise
            else:
                if response.status_code not in self.retry_statuses or attempt == attempts - 1:
                    if response.status_code == 429:
                        self._blocked_hosts.add(urlsplit(url).netloc)
                    response.raise_for_status()
                    return response
            try:
                delay = self._retry_delay(response, attempt)
            except SourceBlockedError:
                self._blocked_hosts.add(urlsplit(url).netloc)
                raise
            finally:
                if response is not None:
                    response.close()
            time.sleep(delay)
