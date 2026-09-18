from __future__ import annotations

import unittest

from mtgwiki import clean_wikitext, parse_wikitext


class ParserTests(unittest.TestCase):
    def test_clean_preserves_templates_by_default(self):
        value = clean_wikitext(
            "'''Tree''' [[Forest|woods]] {{Thing|x=1}}<ref>citation</ref> <i>alive</i>"
        )
        self.assertEqual(value, "Tree woods {{Thing|x=1}} alive")

    def test_nested_templates_and_arbitrary_parameters_are_discovered(self):
        source = """{{Container
|alpha=one
|data=
* {{Item|A|set=X}}
* {{Item|B|note={{Nested|x=y}}}}
|free text
}}"""
        structure = parse_wikitext(source)
        calls = structure["templates"]

        self.assertEqual(calls[0]["name"], "Container")
        self.assertEqual(calls[0]["depth"], 1)
        self.assertIn("data", calls[0]["params"])
        self.assertEqual(calls[0]["params"]["alpha"], "one")
        self.assertEqual(calls[0]["params"]["1"], "free text")
        self.assertEqual([c["name"] for c in calls], ["Container", "Item", "Item", "Nested"])
        self.assertEqual(calls[-1]["depth"], 3)

    def test_parameter_pipe_inside_nested_template_does_not_split_outer(self):
        source = "{{Outer|value={{Inner|a|b=c}}|next=yes}}"
        outer = parse_wikitext(source)["templates"][0]
        self.assertEqual(outer["params"]["value"], "{{Inner|a|b=c}}")
        self.assertEqual(outer["params"]["next"], "yes")

    def test_sections_lists_links_and_tables_are_generic(self):
        source = """== Alpha ==
* [[Target|Visible]]
** Child
[https://example.test External]

{| class="wikitable"
! Name !! Value
|-
| A || '''B'''
|}

=== Beta ===
Text
"""
        structure = parse_wikitext(source)

        self.assertEqual(structure["sections"][0]["heading"]["text"], "Alpha")
        self.assertEqual(structure["sections"][1]["level"], 3)
        self.assertEqual(structure["lists"][1]["depth"], 2)
        self.assertEqual(structure["wikilinks"][0]["target"], "Target")
        self.assertEqual(structure["wikilinks"][0]["text"], "Visible")
        self.assertEqual(structure["external_links"][0]["url"], "https://example.test")
        self.assertEqual(structure["tables"][0]["rows"][0]["cells"][0]["value"]["text"], "Name")
        self.assertEqual(structure["tables"][0]["rows"][1]["cells"][1]["value"]["text"], "B")

    def test_protected_regions_do_not_create_phantom_structure(self):
        source = """<!-- {{Commented|x=1}} [[Hidden]] -->
<nowiki>{{Literal|x=2}} [[Also hidden]]
* not a list</nowiki>
{{Real|x=3}}
[[Visible]]
"""
        structure = parse_wikitext(source)
        self.assertEqual([t["name"] for t in structure["templates"]], ["Real"])
        self.assertEqual([l["target"] for l in structure["wikilinks"]], ["Visible"])
        self.assertEqual(structure["lists"], [])

    def test_raw_and_clean_are_both_preserved(self):
        source = "* '''Name''' [[Thing|label]]<ref>x</ref>"
        item = parse_wikitext(source)["lists"][0]
        self.assertIn("'''Name'''", item["value"]["raw"])
        self.assertEqual(item["value"]["text"], "Name label")


if __name__ == "__main__":
    unittest.main()
