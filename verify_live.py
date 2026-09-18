from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from mtgwiki import Wiki, __version__, write_jsonl


REPORT = Path("mtgwiki_verify_report.json")
OUTPUT_DIR = Path("verify_output")
OUTPUT_DIR.mkdir(exist_ok=True)

# Keep the live test polite. For regular use, replace the User-Agent with your
# own project/contact information.
wiki = Wiki(
    user_agent=f"mtgwiki/{__version__} live-verifier",
    min_interval=0.5,
    maxlag=5,
    cache_ttl=300,
)

report = {
    "started": datetime.now().isoformat(timespec="seconds"),
    "package_version": __version__,
    "python": sys.version,
    "tests": {},
}

passed = warnings = failed = 0


def run(name, func):
    global passed, warnings, failed
    print("\n" + "=" * 78)
    print(name)
    print("=" * 78)
    try:
        result = func()
        status = result.get("status", "PASS")
        if status == "PASS":
            passed += 1
        elif status == "WARN":
            warnings += 1
        else:
            failed += 1
        print(f"[{status}]")
        report["tests"][name] = result
    except Exception as exc:
        failed += 1
        print("[FAIL]", type(exc).__name__, exc)
        report["tests"][name] = {
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


def raw_api():
    data = wiki.api(action="query", meta="siteinfo", siprop="general")
    general = data.get("query", {}).get("general", {})
    print("Sitename:", general.get("sitename"))
    print("Generator:", general.get("generator"))
    return {
        "status": "PASS" if general else "FAIL",
        "sitename": general.get("sitename"),
        "generator": general.get("generator"),
    }


def search_is_ranked_not_identity():
    hits = wiki.search("Bloomburrow", limit=20)
    titles = [h.get("title") for h in hits]
    print("Search results:")
    for title in titles:
        print(" -", title)
    return {
        "status": "PASS" if titles else "FAIL",
        "count": len(titles),
        "titles": titles,
        "note": "Search ranking is intentionally not treated as entity resolution.",
    }


def generic_template_discovery():
    calls = wiki.template_calls("Skyship Weatherlight", section="In-game references")
    top = [c for c in calls if c.get("depth") == 1]
    print("Top-level template calls:")
    for call in top:
        print(" -", call["name"], list(call["params"].keys()))

    # Known live fixture: this confirms arbitrary parameter names and multiline
    # values are surfaced by generic parsing. The library itself has no special
    # code for this template or parameter.
    target = next((c for c in top if c["name"].casefold() == "in-game"), None)
    has_arbitrary_parameter = bool(target and "art" in target["params"])
    nested_count = sum(1 for c in calls if c.get("depth", 1) > 1)

    print("Nested template calls:", nested_count)
    print("Arbitrary parameter surfaced:", has_arbitrary_parameter)
    return {
        "status": "PASS" if has_arbitrary_parameter and nested_count > 0 else "WARN",
        "top_level": [
            {"name": c["name"], "parameter_names": list(c["params"].keys())}
            for c in top
        ],
        "nested_template_count": nested_count,
        "fixture_parameter_found": has_arbitrary_parameter,
    }


def generic_infobox_source_discovery():
    calls = wiki.template_calls("Dack Fayden", section=0)
    top = [c for c in calls if c.get("depth") == 1]
    box = next((c for c in top if c["name"].casefold().startswith("infobox")), None)
    keys = list(box["params"].keys()) if box else []
    print("Top-level templates:", [c["name"] for c in top])
    print("Discovered parameter names:", keys)
    return {
        "status": "PASS" if box and len(keys) >= 5 else "WARN",
        "template": box["name"] if box else None,
        "parameter_names": keys,
        "note": "The verifier knows this fixture; mtgwiki only exposes arbitrary template data.",
    }


def batch_snapshots():
    titles = ["Dack Fayden", "Skyship Weatherlight", "Bloomburrow (plane)"]
    snapshots = wiki.extract_many(titles)
    out = write_jsonl(OUTPUT_DIR / "pages.jsonl", snapshots)

    summary = []
    for snap in snapshots:
        row = {
            "requested": snap["source"]["requested_title"],
            "resolved": snap["source"].get("resolved_title"),
            "exists": snap["exists"],
            "revision_id": snap["source"].get("revision_id"),
            "templates": len((snap.get("structure") or {}).get("templates", [])),
            "links": len((snap.get("structure") or {}).get("wikilinks", [])),
            "sections": len((snap.get("structure") or {}).get("sections", [])),
        }
        summary.append(row)
        print(row)

    good = all(row["exists"] and row["revision_id"] for row in summary)
    structured = all(row["templates"] > 0 or row["sections"] > 0 for row in summary)
    return {
        "status": "PASS" if good and structured else "WARN",
        "output": str(out),
        "pages": summary,
    }


def targeted_section_and_lists():
    sections = wiki.sections("Bloomburrow (plane)")
    names = [s.get("line") for s in sections]
    items = wiki.section_items("Bloomburrow (plane)", "Flora", clean=True)
    print("Section count:", len(names))
    print("List items in selected section:", len(items))
    print("First five clean values:")
    for item in items[:5]:
        print(" -", item["text"])
    return {
        "status": "PASS" if sections and items else "WARN",
        "section_count": len(sections),
        "item_count": len(items),
        "first_5": [item["text"] for item in items[:5]],
        "raw_preserved": bool(items and items[0].get("raw_text")),
    }


def cache_reuse():
    before = wiki.client.stats().copy()
    wiki.api(action="query", meta="siteinfo", siprop="general")
    middle = wiki.client.stats().copy()
    wiki.api(action="query", meta="siteinfo", siprop="general")
    after = wiki.client.stats().copy()

    cache_delta = after["cache_hits"] - middle["cache_hits"]
    http_delta = after["http_requests"] - middle["http_requests"]
    print("Second-call cache hit delta:", cache_delta)
    print("Second-call HTTP delta:", http_delta)
    return {
        "status": "PASS" if cache_delta == 1 and http_delta == 0 else "FAIL",
        "cache_hit_delta": cache_delta,
        "http_request_delta": http_delta,
        "before": before,
        "after": after,
    }


def missing_page():
    snap = wiki.extract("This page definitely does not exist MTGWIKI VERIFY 938475", include=("revision",))
    print("exists:", snap["exists"])
    return {
        "status": "PASS" if snap["exists"] is False else "FAIL",
        "exists": snap["exists"],
        "source": snap["source"],
    }


def recursive_category_traversal():
    # The fixture is MTG-specific because this verifier runs against the
    # package's default site. The feature being exercised is generic MediaWiki
    # category recursion.
    members = wiki.members(
        "Planeswalker characters",
        recurse=1,
        namespace=0,
        cmtype="page",
    )
    titles = [item.get("title") for item in members]
    print("Recursive namespace-0 pages:", len(titles))
    print("Contains Dack Fayden:", "Dack Fayden" in titles)
    print("First ten:", titles[:10])
    return {
        "status": "PASS" if len(titles) >= 50 and "Dack Fayden" in titles else "WARN",
        "count": len(titles),
        "contains_dack_fayden": "Dack Fayden" in titles,
        "first_10": titles[:10],
        "note": "Fixture is site-specific; traversal logic is generic and cycle-safe.",
    }


run("01 - Raw MediaWiki API", raw_api)
run("02 - Search remains ranked search", search_is_ranked_not_identity)
run("03 - Generic arbitrary template parameters", generic_template_discovery)
run("04 - Generic source template discovery", generic_infobox_source_discovery)
run("05 - Batch snapshots + provenance + JSONL", batch_snapshots)
run("06 - Rendered sections + generic list extraction", targeted_section_and_lists)
run("07 - Cache reuse", cache_reuse)
run("08 - Missing page snapshot", missing_page)
run("09 - Recursive category traversal", recursive_category_traversal)

report["client_stats"] = wiki.client.stats()
report["finished"] = datetime.now().isoformat(timespec="seconds")
report["summary"] = {
    "passed": passed,
    "warnings": warnings,
    "failed": failed,
    "total": passed + warnings + failed,
}

REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

print("\n" + "#" * 78)
print("FINAL RESULT")
print("#" * 78)
print("PASS :", passed)
print("WARN :", warnings)
print("FAIL :", failed)
print("Report:", REPORT.resolve())
print("Dataset:", (OUTPUT_DIR / "pages.jsonl").resolve())
