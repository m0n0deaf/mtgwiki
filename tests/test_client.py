from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from mtgwiki import APIError, Client


class FakeResponse:
    def __init__(self, data, *, status_code=200, headers=None):
        self._data = data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.closed = False

    def get(self, url, *, params, timeout):
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        if not self.responses:
            raise AssertionError("No fake response left")
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class ClientTests(unittest.TestCase):
    def test_request_adds_defaults_and_user_agent(self):
        session = FakeSession([FakeResponse({"query": {}})])
        client = Client(
            user_agent="my-tool/1.0 (https://example.test)",
            min_interval=0,
            session=session,
        )
        client.request(action="query", meta="siteinfo")
        params = session.calls[0]["params"]
        self.assertEqual(params["format"], "json")
        self.assertEqual(params["formatversion"], 2)
        self.assertEqual(params["maxlag"], 5)
        self.assertEqual(session.headers["User-Agent"], "my-tool/1.0 (https://example.test)")

    def test_memory_cache_avoids_second_http_request_and_returns_copy(self):
        session = FakeSession([FakeResponse({"query": {"value": [1]}})])
        client = Client(min_interval=0, cache_ttl=300, session=session)
        first = client.request(action="query", meta="siteinfo")
        first["query"]["value"].append(999)
        second = client.request(action="query", meta="siteinfo")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(second["query"]["value"], [1])
        self.assertEqual(client.stats()["cache_hits"], 1)
        self.assertEqual(client.stats()["memory_cache_hits"], 1)

    def test_persistent_cache_survives_new_client(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "cache.sqlite"
            first_session = FakeSession([FakeResponse({"query": {"value": 42}})])
            first = Client(min_interval=0, cache_ttl=300, cache_path=path, session=first_session)
            self.assertEqual(first.request(action="query", meta="siteinfo")["query"]["value"], 42)
            first.close()

            second_session = FakeSession([])
            second = Client(min_interval=0, cache_ttl=300, cache_path=path, session=second_session)
            value = second.request(action="query", meta="siteinfo")
            self.assertEqual(value["query"]["value"], 42)
            self.assertEqual(len(second_session.calls), 0)
            self.assertEqual(second.stats()["disk_cache_hits"], 1)
            second.close()

    def test_iterate_feeds_back_continuation(self):
        session = FakeSession(
            [
                FakeResponse(
                    {
                        "query": {"categorymembers": [{"title": "A"}]},
                        "continue": {"continue": "||", "cmcontinue": "next"},
                    }
                ),
                FakeResponse({"query": {"categorymembers": [{"title": "B"}]}}),
            ]
        )
        client = Client(min_interval=0, cache_ttl=0, session=session)
        batches = list(
            client.iterate(
                action="query",
                list="categorymembers",
                cmtitle="Category:Test",
                cmlimit="max",
                **{"continue": ""},
            )
        )
        self.assertEqual(len(batches), 2)
        self.assertEqual(session.calls[1]["params"]["continue"], "||")
        self.assertEqual(session.calls[1]["params"]["cmcontinue"], "next")

    def test_rate_limiter_waits_between_real_requests(self):
        session = FakeSession([FakeResponse({"a": 1}), FakeResponse({"a": 2})])
        client = Client(min_interval=0.5, cache_ttl=0, session=session)
        with patch("mtgwiki.client.time.monotonic", side_effect=[0.0, 0.1, 0.5]), patch(
            "mtgwiki.client.time.sleep"
        ) as sleep:
            client.request(action="query", meta="one")
            client.request(action="query", meta="two")
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.4, places=6)

    def test_429_respects_retry_after(self):
        session = FakeSession(
            [
                FakeResponse({}, status_code=429, headers={"Retry-After": "7"}),
                FakeResponse({"query": {"ok": True}}),
            ]
        )
        client = Client(min_interval=0, cache_ttl=0, retries=1, session=session)
        with patch("mtgwiki.client.time.sleep") as sleep:
            result = client.request(action="query", meta="siteinfo")
        self.assertTrue(result["query"]["ok"])
        sleep.assert_called_once_with(7.0)

    def test_maxlag_api_error_is_retried(self):
        session = FakeSession(
            [
                FakeResponse({"error": {"code": "maxlag", "info": "Waiting"}}),
                FakeResponse({"query": {"ok": True}}),
            ]
        )
        client = Client(min_interval=0, cache_ttl=0, retries=1, session=session)
        with patch("mtgwiki.client.time.sleep") as sleep:
            result = client.request(action="query", meta="siteinfo")
        self.assertTrue(result["query"]["ok"])
        sleep.assert_called_once_with(5.0)

    def test_permanent_api_error_is_raised(self):
        session = FakeSession([FakeResponse({"error": {"code": "badvalue", "info": "Bad"}})])
        client = Client(min_interval=0, cache_ttl=0, session=session)
        with self.assertRaises(APIError):
            client.request(action="query")

    def test_non_dict_json_is_supported(self):
        session = FakeSession([FakeResponse(["query", ["A"], [], []])])
        client = Client(min_interval=0, cache_ttl=0, session=session)
        self.assertEqual(client.request(action="opensearch", search="query")[1], ["A"])


if __name__ == "__main__":
    unittest.main()
