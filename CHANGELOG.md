# Changelog

## 1.2.1

Public package and documentation polish.

- Updated the README for normal PyPI installation with `pip install mtgwiki`.
- Separated end-user installation from editable development installation.
- Added PyPI, Python, license and release-workflow badges.
- Added public project, repository, issue tracker and changelog links.
- Removed development-specific ChatGPT wording from the public documentation.
- Refreshed package metadata for the public GitHub/PyPI project.
- Expanded `.gitignore` for common Python tooling caches and mtgwiki SQLite sidecar files.

## 1.2.0

Generic research ergonomics inspired by established MediaWiki clients.

- Added lazy `iter_list()` for streaming continued `list=...` modules without materializing everything first.
- Added lazy `iter_members()` for category traversal.
- Extended `members()` with generic recursive subcategory traversal via `recurse=True` or a depth integer such as `recurse=1`.
- Recursive category traversal is cycle-safe and de-duplicates repeated members.
- `namespace=` continues to work during recursion without hiding subcategories needed for traversal.
- Explicit/localized category namespace prefixes are preserved instead of forcing `Category:` in front of them.
- Direct category/backlink/embedded-in helpers now use smaller server-side chunk limits when a small total `limit` is requested.
- Clarified that the library is MediaWiki-generic; MTG Wiki is only the default endpoint/convenience target.
- Added regression tests for lazy iteration, recursion depth, category cycles, de-duplication and request sizing.
- Expanded the live verifier with a recursive-category smoke test.

## 1.1.1

- Fix batched `get_many()` / `extract_many()` queries that request `prop=revisions`.
- Multi-title revision queries now omit `rvlimit`, as required by MediaWiki's revisions API.
- Preserve `rvlimit=1` for single-title requests.
- Add regression tests for both multi-title and single-title revision behavior.

## 1.1.0

Architectural shift toward reusable, domain-neutral data extraction.

- Added `get_many()` with safe multi-title batching.
- Added `extract()` / `extract_many()` JSON-friendly snapshots.
- Added revision/source provenance to snapshots.
- Added generic local wikitext structure discovery:
  - headings/sections
  - arbitrary nested template calls and parameters
  - lists
  - wikilinks
  - external links
  - wiki tables
- Added `template_calls()` and `structure()`.
- Added `images()` and `external_links()` helpers.
- Added `siteinfo()` and `paraminfo()` capability discovery.
- Added JSON and JSONL helpers.
- Added optional persistent SQLite response cache.
- Preserved raw values alongside cleaned representations.
- Kept 1.0 infobox/template helpers as compatibility conveniences.
- Expanded offline regression tests and added a live verification report.

The package still contains no Magic-specific concepts such as planes,
characters, species, flora or depicted cards.

## 1.0.0

- Initial small MediaWiki client.
- Automatic continuation.
- Polite request throttling, maxlag, retry/backoff and memory cache.
- Search, categories, links, backlinks, templates and targeted sections.
