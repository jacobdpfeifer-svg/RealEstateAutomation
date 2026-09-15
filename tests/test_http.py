from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import unittest
from unittest.mock import patch

import requests

from leads.http import SourceBlockedError, SourceSession, safe_error


def response(status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result.url = "https://example.test/search?owner=private"
    result.headers.update(headers or {})
    result._content = b""
    result._content_consumed = True
    return result


class TestHTTPPolicy(unittest.TestCase):
    def setUp(self):
        SourceSession._last_request.clear()
        SourceSession._blocked_hosts.clear()
        self.addCleanup(SourceSession._last_request.clear)
        self.addCleanup(SourceSession._blocked_hosts.clear)

    def test_transient_get_retries_with_retry_after(self):
        with SourceSession(min_interval=0) as session:
            with patch("requests.Session.request", side_effect=[response(429, {"Retry-After": "2"}), response()]) as send, patch("leads.http.time.sleep") as sleep:
                self.assertEqual(session.get("https://example.test").status_code, 200)
                self.assertEqual(send.call_count, 2)
                sleep.assert_called_once_with(2)
                self.assertEqual(send.call_args.kwargs["timeout"], (10, 60))

    def test_post_never_replayed(self):
        for failure in [response(503), requests.Timeout("private payload")]:
            with self.subTest(failure=type(failure).__name__):
                with SourceSession(min_interval=0) as session:
                    with patch("requests.Session.request", side_effect=[failure]) as send:
                        with self.assertRaises(requests.RequestException):
                            session.post("https://example.test", json={"owner": "private"})
                        self.assertEqual(send.call_count, 1)

    def test_timeout_retry_is_bounded(self):
        with SourceSession(min_interval=0) as session:
            with patch("requests.Session.request", side_effect=requests.Timeout()) as send, patch("leads.http.time.sleep"):
                with self.assertRaises(requests.Timeout):
                    session.get("https://example.test")
                self.assertEqual(send.call_count, 3)

    def test_forbidden_blocks_later_requests_across_instances(self):
        with patch("requests.Session.send", return_value=response(403)) as send:
            with SourceSession(min_interval=0) as first, SourceSession(min_interval=0) as second:
                with self.assertRaises(requests.HTTPError):
                    first.get("https://example.test")
                with self.assertRaises(SourceBlockedError):
                    second.get("https://example.test/again")
                self.assertEqual(send.call_count, 1)

    def test_pacing_shared_across_sessions(self):
        with patch("requests.Session.send", return_value=response()), patch("leads.http.time.monotonic", return_value=100), patch("leads.http.time.sleep") as sleep:
            with SourceSession(min_interval=1) as first, SourceSession(min_interval=1) as second:
                first.get("https://example.test")
                second.get("https://example.test/next")
            sleep.assert_called_once_with(1)

    def test_long_retry_after_stops_instead_of_retrying_early(self):
        with SourceSession(min_interval=0) as session:
            with patch("requests.Session.request", return_value=response(429, {"Retry-After": "3600"})) as send, patch("leads.http.time.sleep") as sleep:
                with self.assertRaises(SourceBlockedError):
                    session.get("https://example.test")
                self.assertEqual(send.call_count, 1)
                sleep.assert_not_called()

    def test_retry_after_http_date(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=30)
        delay = SourceSession._retry_delay(response(503, {"Retry-After": format_datetime(future)}), 0)
        self.assertGreater(delay, 28)
        self.assertLessEqual(delay, 30)

    def test_safe_error_contains_no_url_body_or_secret(self):
        error = requests.HTTPError("owner=private Authorization=secret", response=response(403))
        self.assertEqual(safe_error(error), "HTTPError HTTP 403")
