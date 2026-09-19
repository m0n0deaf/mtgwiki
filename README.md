# mtgwiki

[![PyPI version](https://img.shields.io/pypi/v/mtgwiki.svg)](https://pypi.org/project/mtgwiki/)
[![Python versions](https://img.shields.io/pypi/pyversions/mtgwiki.svg)](https://pypi.org/project/mtgwiki/)
[![License: MIT](https://img.shields.io/pypi/l/mtgwiki.svg)](https://github.com/m0n0deaf/mtgwiki/blob/main/LICENSE)
[![Publish to PyPI](https://github.com/m0n0deaf/mtgwiki/actions/workflows/release.yml/badge.svg)](https://github.com/m0n0deaf/mtgwiki/actions/workflows/release.yml)

`mtgwiki` is a lightweight Python package for getting **useful, reusable data** out of MediaWiki Action API sites without teaching the package what the wiki's subject matter means.

MTG Wiki is the default endpoint and the original use case, but the transport, traversal, parsing and snapshot layers are domain-neutral.

The package does **not** contain functions such as `get_plane()`, `get_character()`, `flora()` or `depicted_cards()`. Instead it exposes generic MediaWiki building blocks and a generic snapshot/structure layer that downstream projects can interpret however they want.

## Live showcase

Explore real MediaWiki data extracted with mtgwiki:

https://m0n0deaf.github.io/mtgwiki/

## Installation

```bash
python -m pip install mtgwiki
```

Requires Python 3.10 or newer.

## Quick start

```python
from mtgwiki import Wiki

wiki = Wiki()

page = wiki.get("Skyship Weatherlight")
print(page["title"])

hits = wiki.search("Bloomburrow", limit=20)
for hit in hits:
    print(hit["title"])
```

Search results are ranked search results. `search()` does not pretend the first result is the entity you meant.

For category-based research, recursive traversal is built in:

```python
pages = wiki.members(
    "Planeswalker characters",
    recurse=True,
    namespace=0,
    cmtype="page",
)

for page in pages:
    print(page["title"])
```

## Design rule

> If a feature needs to know Magic semantics, it does not belong in `mtgwiki`.

The package has three jobs:

1. **Acquire** data politely and efficiently from MediaWiki.
2. **Structure** generic wiki syntax such as templates, parameters, sections, lists, tables and links.
3. **Export** JSON-friendly snapshots that preserve source/provenance and raw data.

## User-Agent and other MediaWiki sites

For sustained API use, identify your own project in the User-Agent:

```python
from mtgwiki import Wiki

wiki = Wiki(
    user_agent="my-project/0.1 (https://example.com/contact)"
)
```

Point the same client at another MediaWiki installation by supplying its Action API endpoint:

```python
wiki = Wiki(
    api_url="https://www.mediawiki.org/w/api.php",
    user_agent="my-project/0.1 (https://example.com/contact)",
)
```

The package name and default URL are conveniences; the public data model contains no Magic-specific entity types.

## Compatibility

The client targets modern MediaWiki Action API installations and always requests JSON with `formatversion=2`. Revision-content handling supports main-slot responses and includes fallbacks for older response shapes. Rendered section discovery prefers `tocdata` and falls back to the older `sections` output. Use `siteinfo()` and `paraminfo()` when a consuming project needs to discover site-specific capabilities rather than assume them.

## Generic structure discovery

Ask the package what syntax exists instead of hard-coding what you expect:

```python
structure = wiki.structure(
    "Skyship Weatherlight",
    section="In-game references",
)

for template in structure["templates"]:
    print(template["name"])
    print(template["params"].keys())
```

A template call is returned generically, for example:

```python
{
    "name": "Some template",
    "depth": 1,
    "raw": "{{Some template|x=1|data=...}}",
    "params": {
        "x": "1",
        "data": "...",
    },
    "params_clean": {
        "x": "1",
        "data": "...",
    },
    "parameters": [...],
}
```

The package does not assign meaning to `x`, `data`, `art`, `species`, `job1` or any other parameter name.

## Snapshots: easiest way to build reusable datasets

```python
snapshot = wiki.extract("Dack Fayden")

print(snapshot.keys())
print(snapshot["source"])
print(snapshot["data"])
print(snapshot["structure"].keys())
```

A snapshot contains:

```text
schema_version
exists
source
  api_url
  requested_title
  resolved_title
  pageid
  revision_id
  revision_timestamp
  retrieved_at
  redirects
page
revision
data
  categories
  links
  templates
  images
  external_links
  pageprops
content
  raw
  text
structure
  sections
  templates
  wikilinks
  external_links
  lists
  tables
raw
  page
  normalized
  redirects
  converted
```

Raw source is deliberately retained. Cleaned/normalized values are conveniences, not replacements for the source.

## Batch extraction

MediaWiki accepts multiple titles in one query. `extract_many()` batches up to 50 titles per request group instead of making one independent request per title.

```python
pages = wiki.extract_many(
    [
        "Urza",
        "Dack Fayden",
        "Skyship Weatherlight",
        "Bloomburrow (plane)",
    ]
)
```

You can request a smaller snapshot when you do not need everything:

```python
pages = wiki.extract_many(
    ["Urza", "Dack Fayden"],
    include=("revision", "categories", "wikitext", "structure"),
)
```

Supported include values:

```text
revision
categories
links
templates
images
external_links
pageprops
wikitext
structure
```

## JSON / JSONL

```python
from mtgwiki import write_json, write_jsonl

write_json("one-page.json", wiki.extract("Urza"))
write_jsonl("dataset.jsonl", wiki.extract_many(["Urza", "Karn", "Squee"]))
```

JSONL is convenient for larger datasets because every page is one independent line.

## Raw API is always available

The high-level helpers never lock you out of MediaWiki itself:

```python
data = wiki.api(
    action="query",
    meta="siteinfo",
    siprop="general|namespaces",
)
```

Continuation:

```python
for response in wiki.iter_api(
    action="query",
    list="categorymembers",
    cmtitle="Category:Example",
    cmlimit="max",
):
    print(response)
```

Or collect common list modules directly:

```python
members = wiki.members("Example category")
backlinks = wiki.backlinks("Example page")
embeds = wiki.embedded_in("Template:Example")
```

These are MediaWiki concepts, not domain semantics.

## Lazy list iteration

For large list modules you can stream items instead of building one large list in memory:

```python
for item in wiki.iter_list(
    "allpages",
    aplimit="max",
    apnamespace=0,
):
    print(item["title"])
```

`list()` remains available when collecting everything is more convenient.

## Recursive categories

Category trees are a generic MediaWiki structure. `members()` can now traverse them without every research script reimplementing a queue and cycle guard:

```python
pages = wiki.members(
    "Example root category",
    recurse=True,
    namespace=0,
    cmtype="page",
)

for page in pages:
    print(page["title"])
```

Use an integer to limit depth. `recurse=1` includes members of immediate subcategories but does not descend further:

```python
pages = wiki.members(
    "Example root category",
    recurse=1,
    namespace=0,
    cmtype="page",
)
```

For very large category trees, use `iter_members()` to stream the same traversal lazily. Recursive traversal is cycle-safe and de-duplicates members that appear through multiple category paths.

## Sections

For rendered section discovery the package asks MediaWiki for `tocdata` and falls back to the older `sections` response when necessary:

```python
for section in wiki.sections("Bloomburrow (plane)"):
    print(section["line"])
```

Fetch only one named section:

```python
text = wiki.section("Bloomburrow (plane)", "Flora")
```

Extract generic list items without losing the original markup:

```python
items = wiki.section_items(
    "Bloomburrow (plane)",
    "Flora",
    clean=True,
)

for item in items:
    print(item["raw_text"])
    print(item["text"])
```

The method knows only that it is reading a list in a section. It does not know what "Flora" means.

## Wikitext structure parser

The built-in parser is deliberately conservative and dependency-free. It discovers:

- section headings
- nested template calls and arbitrary parameters
- wikilinks
- external links
- list items and nesting depth
- MediaWiki tables

Use it on any string:

```python
from mtgwiki import parse_wikitext

structure = parse_wikitext("{{Thing|a=1}}\n* [[Target|Label]]")
```

It is **not** intended to reproduce MediaWiki's renderer. When exact rendered output matters, use `wiki.parse()` / `wiki.section()` and let the server parse the page. Raw source and spans are kept so a later project can always fall back to the original material.

## Revisions and provenance

`extract()` records the revision ID, timestamp and SHA-1 where available. This makes exported data traceable to the wiki revision it came from.

```python
snapshot = wiki.extract("Urza", include=("revision", "wikitext"))
print(snapshot["source"]["revision_id"])
print(snapshot["revision"]["sha1"])
```

## API capability discovery

Do not assume every MediaWiki installation exposes exactly the same features:

```python
info = wiki.siteinfo()
params = wiki.paraminfo(["query", "parse"])
```

## Polite API behavior

The client defaults to:

```text
serial requests only
min_interval = 0.5 seconds
maxlag = 5
Retry-After support
exponential retry/backoff
memory cache
```

Statistics:

```python
print(wiki.client.stats())
```

Optional persistent cache:

```python
wiki = Wiki(
    cache_path=".mtgwiki-cache.sqlite",
    cache_ttl=24 * 60 * 60,
)
```

The SQLite cache is optional and uses only Python's standard library.

## Compatibility helpers

The 1.0 helpers `infobox()`, `infobox_fields()`, `templates_with_params()` and `template_params()` remain available so existing experiments keep working. New code should generally prefer `template_calls()` or `structure()` because those do not assume a particular template convention.

## Development

Clone the repository and install it in editable mode:

```powershell
git clone https://github.com/m0n0deaf/mtgwiki.git
cd mtgwiki

py -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -e .
```

Run the offline test suite:

```powershell
python -m unittest discover -s tests -v
```

Then run the optional live verifier against the configured MediaWiki endpoint:

```powershell
python verify_live.py
```

It creates:

```text
mtgwiki_verify_report.json
verify_output/pages.jsonl
```

The report can be attached to bug reports or used when troubleshooting live-site compatibility.

## Project links

- PyPI: https://pypi.org/project/mtgwiki/
- Source: https://github.com/m0n0deaf/mtgwiki
- Issues: https://github.com/m0n0deaf/mtgwiki/issues
- Changelog: https://github.com/m0n0deaf/mtgwiki/blob/main/CHANGELOG.md
