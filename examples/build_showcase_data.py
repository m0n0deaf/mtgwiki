"""Regenerate the static showcase: python examples/build_showcase_data.py.

Only this maintainer-run script contacts MediaWiki. Commit its JSON output to
publish a fixed dataset; rerunning deliberately retrieves newer wiki revisions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from mtgwiki import Wiki, __version__, parse_wikitext, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "docs/data/showcase.json")
    args = parser.parse_args()
    wiki = Wiki(user_agent=f"mtgwiki/{__version__} showcase (https://github.com/m0n0deaf/mtgwiki)",
                min_interval=0.5, cache_ttl=0)
    try:
        titles = ["Urza", "Dack Fayden", "Skyship Weatherlight", "Bloomburrow (plane)"]
        print("Fetching curated snapshots...", flush=True)
        snapshots = wiki.extract_many(titles)
        if any(not item["exists"] for item in snapshots):
            raise RuntimeError("A showcase title is unavailable; previous JSON has not been replaced")
        root = "Planeswalker characters"
        print("Traversing category graph...", flush=True)
        direct = wiki.members(root, namespace=0, cmtype="page")
        members = wiki.members(root, recurse=True, namespace=0, cmtype="page")
        subcategories = wiki.members(root, cmtype="subcat")
        rights = wiki.siteinfo(("rightsinfo",)).get("rightsinfo", {})
        sample = "{{Infobox character\n|species=Human\n|spark=[[Planeswalker]]\n}}"
        data = {
            "schema_version": 1,
            "package_version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "api_url": wiki.api_url,
            "rights": rights,
            "snapshots": snapshots,
            "template_example": {"label": "Illustrative wikitext, parsed with mtgwiki (not a page quotation)",
                                 "raw": sample, "structure": parse_wikitext(sample)},
            "category": {"title": root, "direct_members": direct, "members": members,
                         "immediate_subcategories": subcategories,
                         "retrieved_at": datetime.now(timezone.utc).isoformat(),
                         "query": {"recurse": True, "namespace": 0, "cmtype": "page"}},
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(".tmp")
        write_json(temporary, data)
        temporary.replace(args.output)
        print(f"Saved {len(snapshots)} snapshots and {len(members)} category members to {args.output}")
    finally:
        wiki.client.close()


if __name__ == "__main__":
    main()
