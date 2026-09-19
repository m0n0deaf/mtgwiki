from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mtgwiki import Client, Wiki, clean_wikitext, parse_wikitext
from test_client import FakeResponse, FakeSession
from test_wiki import FakeClient


class MaintenanceTests(unittest.TestCase):
    def test_case_distinct_titles_remain_distinct(self):
        titles = ["Example", "EXAMPLE", "example"]
        pages = [{"pageid": i, "title": title} for i, title in enumerate(titles, 1)]
        wiki = Wiki(client=FakeClient([{"query": {"pages": pages}}]))
        self.assertEqual(wiki.get_many(titles, props=("info",)), pages)

    def test_case_distinct_redirect_sources_remain_distinct(self):
        pages = [{"title": "One"}, {"title": "Two"}]
        client = FakeClient([{"query": {"pages": pages, "redirects": [
            {"from": "Alias", "to": "One"}, {"from": "ALIAS", "to": "Two"},
        ]}}])
        self.assertEqual(Wiki(client=client).get_many(["Alias", "ALIAS"]), pages)

    def test_case_distinct_categories_are_both_traversed(self):
        client = FakeClient([
            {"query": {"categorymembers": [
                {"ns": 14, "title": "Category:Child"},
                {"ns": 14, "title": "Category:CHILD"},
            ]}},
            {"query": {"categorymembers": [{"ns": 0, "title": "Page"}]}},
            {"query": {"categorymembers": [{"ns": 0, "title": "PAGE"}]}},
        ])
        result = Wiki(client=client).members("Root", recurse=True, namespace=0)
        self.assertEqual([item["title"] for item in result], ["Page", "PAGE"])

    def test_get_invalid_title_returns_none(self):
        wiki = Wiki(client=FakeClient([{"query": {"pages": [
            {"title": "Talk:", "invalid": True, "invalidreason": "Empty title"},
        ]}}]))
        self.assertIsNone(wiki.get("Talk:"))

    def test_extract_invalid_title_does_not_exist(self):
        wiki = Wiki(client=FakeClient([{"query": {"pages": [
            {"title": "Talk:", "invalid": True, "invalidreason": "Empty title"},
        ]}}]))
        snapshot = wiki.extract("Talk:")
        self.assertFalse(snapshot["exists"])
        self.assertEqual(snapshot["raw"]["page"]["invalidreason"], "Empty title")

    def test_disk_promotion_preserves_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite"
            with patch("mtgwiki.client.time.time", return_value=100), patch(
                "mtgwiki.client.time.monotonic", return_value=100
            ):
                with Client(cache_path=path, cache_ttl=10, min_interval=0,
                            session=FakeSession([FakeResponse({"value": "old"})])) as client:
                    client.request(action="query")
            session = FakeSession([FakeResponse({"value": "fresh"})])
            with patch("mtgwiki.client.time.time", return_value=109) as wall, patch(
                "mtgwiki.client.time.monotonic", return_value=200
            ) as monotonic:
                with Client(cache_path=path, cache_ttl=10, min_interval=0, session=session) as client:
                    self.assertEqual(client.request(action="query"), {"value": "old"})
                    wall.return_value = 111
                    monotonic.return_value = 202
                    self.assertEqual(client.request(action="query"), {"value": "fresh"})

    def test_comment_separators_preserve_raw_parameter(self):
        value = "a<!-- |fake=b -->"
        source = "{{T|x=" + value + "|y=c}}"
        call = parse_wikitext(source)["templates"][0]
        self.assertEqual(call["params"], {"x": value, "y": "c"})
        self.assertEqual(call["raw"], source)

    def test_nowiki_separators_and_braces_preserve_raw_parameter(self):
        value = "<nowiki>|fake=b {{</nowiki>"
        source = "{{T|" + value + "|y=c}}"
        call = parse_wikitext(source)["templates"][0]
        self.assertEqual(call["params"], {"1": value, "y": "c"})
        self.assertEqual(call["parameters"][0]["value"]["raw"], value)
        self.assertEqual(call["raw"], source)

    def test_nested_template_removal_preserves_surrounding_prose(self):
        source = "before {{Outer|{{Inner}}}} middle {{Other}} after"
        self.assertEqual(clean_wikitext(source, remove_templates=True), "before middle after")

    def test_property_merge_avoids_quadratic_record_comparisons(self):
        class CountedRecord(dict):
            comparisons = 0

            def __eq__(self, other):
                type(self).comparisons += 1
                return super().__eq__(other)

        first = [CountedRecord(ns=0, title=f"Page {i}") for i in range(100)]
        second = [CountedRecord(ns=0, title=f"Page {i}") for i in range(50, 150)]
        client = FakeClient([
            {"query": {"pages": [{"pageid": 1, "links": first}]}, "continue": {"plcontinue": "next"}},
            {"query": {"pages": [{"pageid": 1, "links": second}]}},
        ])
        result = Wiki(client=client).pages(titles="Root", prop="links")[0]["links"]
        self.assertEqual([item["title"] for item in result], [f"Page {i}" for i in range(150)])
        self.assertLess(CountedRecord.comparisons, 1000)

    def test_property_merge_preserves_metadata_and_unfamiliar_shapes(self):
        first = [{"title": "A", "ns": 0}, {"nested": [1]}, "url"]
        second = [{"ns": 0, "title": "A"}, {"title": "A", "ns": 1},
                  {"nested": [1]}, {"nested": [2]}, "url", "other"]
        expected = first + [second[1], second[3], "other"]
        Wiki._extend_unique(first, second)
        self.assertEqual(first, expected)
