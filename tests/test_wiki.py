from __future__ import annotations

import unittest

from mtgwiki import APIError, Wiki


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.api_url = "https://example.test/api.php"

    def request(self, **params):
        self.calls.append(dict(params))
        if not self.responses:
            raise AssertionError(f"No fake response left for {params}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def iterate(self, **params):
        base = dict(params)
        continuation = {}
        while True:
            data = self.request(**{**base, **continuation})
            yield data
            if not isinstance(data, dict):
                break
            continuation = data.get("continue") or {}
            if not continuation:
                break


class CoreTests(unittest.TestCase):
    def test_list_collects_continuation(self):
        client = FakeClient(
            [
                {
                    "query": {"categorymembers": [{"title": "A"}]},
                    "continue": {"continue": "||", "cmcontinue": "x"},
                },
                {"query": {"categorymembers": [{"title": "B"}]}},
            ]
        )
        wiki = Wiki(client=client)
        self.assertEqual([x["title"] for x in wiki.list("categorymembers")], ["A", "B"])

    def test_iter_list_is_lazy_across_continuation_and_honors_total_limit(self):
        client = FakeClient(
            [
                {
                    "query": {"search": [{"title": "A"}, {"title": "B"}]},
                    "continue": {"continue": "-||", "sroffset": 2},
                },
                {"query": {"search": [{"title": "C"}]}},
            ]
        )
        wiki = Wiki(client=client)
        iterator = wiki.iter_list("search", limit=2, srsearch="x")
        self.assertEqual(next(iterator)["title"], "A")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(next(iterator)["title"], "B")
        with self.assertRaises(StopIteration):
            next(iterator)
        self.assertEqual(len(client.calls), 1)

    def test_members_can_recurse_through_subcategories_cycle_safely(self):
        client = FakeClient(
            [
                {
                    "query": {
                        "categorymembers": [
                            {"pageid": 1, "ns": 0, "title": "A", "type": "page"},
                            {"pageid": 10, "ns": 14, "title": "Category:Child", "type": "subcat"},
                        ]
                    }
                },
                {
                    "query": {
                        "categorymembers": [
                            {"pageid": 2, "ns": 0, "title": "B", "type": "page"},
                            {"pageid": 11, "ns": 14, "title": "Category:Root", "type": "subcat"},
                            {"pageid": 1, "ns": 0, "title": "A", "type": "page"},
                        ]
                    }
                },
            ]
        )
        wiki = Wiki(client=client)
        members = wiki.members("Root", recurse=True, namespace=0, cmtype="page")
        self.assertEqual([item["title"] for item in members], ["A", "B"])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["cmtitle"], "Category:Root")
        self.assertEqual(client.calls[1]["cmtitle"], "Category:Child")
        self.assertEqual(client.calls[0]["cmtype"], "page|subcat")
        self.assertNotIn("cmnamespace", client.calls[0])

    def test_members_recurse_integer_limits_depth(self):
        client = FakeClient(
            [
                {
                    "query": {
                        "categorymembers": [
                            {"pageid": 1, "ns": 0, "title": "Root page", "type": "page"},
                            {"pageid": 10, "ns": 14, "title": "Category:Child", "type": "subcat"},
                        ]
                    }
                },
                {
                    "query": {
                        "categorymembers": [
                            {"pageid": 2, "ns": 0, "title": "Child page", "type": "page"},
                            {"pageid": 11, "ns": 14, "title": "Category:Grandchild", "type": "subcat"},
                        ]
                    }
                },
            ]
        )
        wiki = Wiki(client=client)
        members = wiki.members("Root", recurse=1, namespace=0, cmtype="page")
        self.assertEqual([item["title"] for item in members], ["Root page", "Child page"])
        self.assertEqual(len(client.calls), 2)

    def test_members_direct_limit_uses_small_server_chunk(self):
        client = FakeClient([{"query": {"categorymembers": [{"title": "A", "ns": 0}]}}])
        wiki = Wiki(client=client)
        wiki.members("Root", limit=10)
        self.assertEqual(client.calls[0]["cmlimit"], 10)

    def test_members_preserves_explicit_localized_namespace_prefix(self):
        client = FakeClient([{"query": {"categorymembers": []}}])
        wiki = Wiki(client=client)
        wiki.members("Kategorie:Beispiel")
        self.assertEqual(client.calls[0]["cmtitle"], "Kategorie:Beispiel")

    def test_pages_merges_repeated_page_across_continuation(self):
        client = FakeClient(
            [
                {
                    "query": {"pages": [{"pageid": 1, "title": "Page", "links": [{"title": "A"}]}]},
                    "continue": {"continue": "||", "plcontinue": "x"},
                },
                {"query": {"pages": [{"pageid": 1, "title": "Page", "links": [{"title": "B"}]}]}},
            ]
        )
        wiki = Wiki(client=client)
        page = wiki.pages(titles="Page", prop="links")[0]
        self.assertEqual(page["links"], [{"title": "A"}, {"title": "B"}])

    def test_get_missing_returns_none(self):
        client = FakeClient([{"query": {"pages": [{"title": "Nope", "missing": True}]}}])
        wiki = Wiki(client=client)
        self.assertIsNone(wiki.get("Nope"))

    def test_get_many_maps_normalization_and_redirects(self):
        client = FakeClient(
            [
                {
                    "query": {
                        "normalized": [{"from": "some_page", "to": "Some page"}],
                        "redirects": [{"from": "Some page", "to": "Target page"}],
                        "pages": [
                            {"pageid": 7, "title": "Target page"},
                            {"pageid": 8, "title": "Other"},
                        ],
                    }
                }
            ]
        )
        wiki = Wiki(client=client)
        pages = wiki.get_many(["some_page", "Other"], props=("info",))
        self.assertEqual(pages[0]["title"], "Target page")
        self.assertEqual(pages[1]["title"], "Other")

    def test_get_many_batches_at_requested_size(self):
        client = FakeClient(
            [
                {"query": {"pages": [{"pageid": 1, "title": "A"}, {"pageid": 2, "title": "B"}]}},
                {"query": {"pages": [{"pageid": 3, "title": "C"}]}},
            ]
        )
        wiki = Wiki(client=client)
        pages = wiki.get_many(["A", "B", "C"], props=("info",), batch_size=2)
        self.assertEqual([page["title"] for page in pages], ["A", "B", "C"])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["titles"], "A|B")
        self.assertEqual(client.calls[1]["titles"], "C")

    def test_get_many_revisions_omits_rvlimit_for_multi_title_query(self):
        client = FakeClient([
            {
                "query": {
                    "pages": [
                        {"pageid": 1, "title": "A", "revisions": [{"revid": 10}]},
                        {"pageid": 2, "title": "B", "revisions": [{"revid": 20}]},
                    ]
                }
            }
        ])
        wiki = Wiki(client=client)
        pages = wiki.get_many(["A", "B"], props=("revisions",))
        self.assertEqual([page["title"] for page in pages], ["A", "B"])
        self.assertNotIn("rvlimit", client.calls[0])

    def test_get_many_revisions_keeps_rvlimit_for_single_title_query(self):
        client = FakeClient([
            {"query": {"pages": [{"pageid": 1, "title": "A", "revisions": [{"revid": 10}]}]}}
        ])
        wiki = Wiki(client=client)
        pages = wiki.get_many(["A"], props=("revisions",))
        self.assertEqual(pages[0]["title"], "A")
        self.assertEqual(client.calls[0].get("rvlimit"), 1)

    def test_public_api_has_no_domain_specific_shortcuts(self):
        for name in ("plane", "planes", "flora", "species", "character", "characters", "depicted_cards"):
            self.assertFalse(hasattr(Wiki, name), name)

    def test_search_does_not_resolve_identity(self):
        client = FakeClient([{"query": {"search": [{"title": "Name"}, {"title": "Name (other)"}]}}])
        wiki = Wiki(client=client)
        hits = wiki.search("Name", limit=10)
        self.assertEqual([h["title"] for h in hits], ["Name", "Name (other)"])
        self.assertEqual(client.calls[0]["srlimit"], 10)

    def test_sections_prefers_tocdata_and_falls_back(self):
        client = FakeClient([{"parse": {}}, {"parse": {"sections": [{"index": "1", "line": "X"}]}}])
        wiki = Wiki(client=client)
        self.assertEqual(wiki.sections("Page")[0]["line"], "X")
        self.assertEqual([call["prop"] for call in client.calls], ["tocdata", "sections"])

    def test_sections_falls_back_after_api_error(self):
        client = FakeClient([APIError("badvalue", "unsupported"), {"parse": {"sections": []}}])
        wiki = Wiki(client=client)
        self.assertEqual(wiki.sections("Page"), [])

    def test_section_items_preserve_raw_and_clean(self):
        wiki = Wiki(client=FakeClient([]))
        wiki.section = lambda *args, **kwargs: "* '''A''' [[Thing|label]]<ref>x</ref>\n** Child"
        items = wiki.section_items("Page", "Part", clean=True)
        self.assertIn("'''A'''", items[0]["raw_text"])
        self.assertEqual(items[0]["text"], "A label")
        self.assertEqual(items[1]["depth"], 2)

    def test_template_calls_are_generic(self):
        wiki = Wiki(client=FakeClient([]))
        wiki.wikitext = lambda title: "{{Whatever|x=1|data={{Nested|a|b=2}}}}"
        calls = wiki.template_calls("Page")
        self.assertEqual(calls[0]["name"], "Whatever")
        self.assertEqual(calls[0]["params"]["x"], "1")
        self.assertEqual(calls[1]["name"], "Nested")


class ExtractTests(unittest.TestCase):
    def _response(self):
        return {
            "query": {
                "pages": [
                    {
                        "pageid": 12,
                        "ns": 0,
                        "title": "Example",
                        "fullurl": "https://example.test/wiki/Example",
                        "length": 100,
                        "categories": [{"title": "Category:One"}],
                        "links": [{"title": "Linked"}],
                        "templates": [{"title": "Template:Box"}],
                        "images": [{"title": "File:Pic.jpg"}],
                        "extlinks": [{"url": "https://outside.test"}],
                        "pageprops": {"displaytitle": "Example"},
                        "revisions": [
                            {
                                "revid": 99,
                                "parentid": 98,
                                "timestamp": "2026-01-02T03:04:05Z",
                                "sha1": "abc",
                                "slots": {
                                    "main": {
                                        "contentmodel": "wikitext",
                                        "contentformat": "text/x-wiki",
                                        "content": "== Part ==\n* [[Linked|Label]]\n{{Box|key=value}}",
                                    }
                                },
                            }
                        ],
                    }
                ]
            }
        }

    def test_extract_produces_json_friendly_snapshot_with_provenance(self):
        client = FakeClient([self._response()])
        wiki = Wiki(client=client)
        snap = wiki.extract("Example")

        self.assertTrue(snap["exists"])
        self.assertEqual(snap["source"]["requested_title"], "Example")
        self.assertEqual(snap["source"]["revision_id"], 99)
        self.assertEqual(snap["revision"]["sha1"], "abc")
        self.assertEqual(snap["data"]["categories"], ["Category:One"])
        self.assertEqual(snap["data"]["external_links"], ["https://outside.test"])
        self.assertIn("{{Box|key=value}}", snap["content"]["raw"])
        self.assertEqual(snap["structure"]["templates"][0]["params"]["key"], "value")
        self.assertEqual(snap["structure"]["lists"][0]["value"]["text"], "Label")
        self.assertEqual(snap["raw"]["page"]["pageid"], 12)

    def test_extract_can_request_small_subset(self):
        client = FakeClient([self._response()])
        wiki = Wiki(client=client)
        snap = wiki.extract("Example", include=("revision",))
        self.assertEqual(snap["data"], {})
        self.assertIsNone(snap["content"])
        self.assertIsNone(snap["structure"])
        self.assertNotIn("content", client.calls[0]["rvprop"].split("|"))

    def test_extract_missing_is_explicit(self):
        client = FakeClient([{"query": {"pages": [{"ns": 0, "title": "Missing", "missing": True}]}}])
        wiki = Wiki(client=client)
        snap = wiki.extract("Missing")
        self.assertFalse(snap["exists"])
        self.assertEqual(snap["source"]["requested_title"], "Missing")


    def test_extract_many_batches(self):
        client = FakeClient([
            {"query": {"pages": [{"pageid": 1, "title": "A", "revisions": []}, {"pageid": 2, "title": "B", "revisions": []}]}},
            {"query": {"pages": [{"pageid": 3, "title": "C", "revisions": []}]}},
        ])
        wiki = Wiki(client=client)
        snaps = wiki.extract_many(["A", "B", "C"], include=("revision",), batch_size=2)
        self.assertEqual([s["page"]["title"] for s in snaps], ["A", "B", "C"])
        self.assertEqual(len(client.calls), 2)

    def test_extract_many_revision_batch_omits_rvlimit(self):
        client = FakeClient([
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 1,
                            "title": "A",
                            "revisions": [{"revid": 10, "timestamp": "2026-01-01T00:00:00Z"}],
                        },
                        {
                            "pageid": 2,
                            "title": "B",
                            "revisions": [{"revid": 20, "timestamp": "2026-01-02T00:00:00Z"}],
                        },
                    ]
                }
            }
        ])
        wiki = Wiki(client=client)
        snaps = wiki.extract_many(["A", "B"], include=("revision",))
        self.assertEqual([s["source"]["revision_id"] for s in snaps], [10, 20])
        self.assertNotIn("rvlimit", client.calls[0])

    def test_extract_single_revision_keeps_rvlimit_one(self):
        client = FakeClient([
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 1,
                            "title": "A",
                            "revisions": [{"revid": 10, "timestamp": "2026-01-01T00:00:00Z"}],
                        }
                    ]
                }
            }
        ])
        wiki = Wiki(client=client)
        snap = wiki.extract("A", include=("revision",))
        self.assertEqual(snap["source"]["revision_id"], 10)
        self.assertEqual(client.calls[0].get("rvlimit"), 1)

    def test_extract_rejects_domain_specific_unknown_include(self):
        wiki = Wiki(client=FakeClient([]))
        with self.assertRaises(ValueError):
            wiki.extract("Page", include=("flora",))


if __name__ == "__main__":
    unittest.main()
