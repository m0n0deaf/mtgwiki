# Design contract

`mtgwiki` is intentionally a **MediaWiki data-access and structure package**, not a Magic knowledge model.

## What belongs here

- HTTP/API transport
- request etiquette, retries and caching
- MediaWiki continuation
- lazy iteration over large list modules
- generic, cycle-safe category traversal
- search and raw query access
- multi-title batching
- redirects/normalization handling
- revision/source provenance
- generic page properties
- generic wikitext syntax discovery
- raw + cleaned representations
- JSON/JSONL export

## What does not belong here

The package must not decide that a value means a plane, character, species, class, flora entry, depicted card, faction, location or any other Magic-domain concept.

A downstream project may make those interpretations using the generic data returned by this package.

## Practical test for new features

Before adding a helper, ask:

> Would this helper still make sense for an unrelated MediaWiki site?

If yes, it is a candidate for `mtgwiki`.
If no, it belongs in the consuming project.

## Raw data rule

Normalization must not destroy the source. Where the package cleans or structures text, the original raw representation should remain available either directly on the object or in the snapshot's raw/content fields.

## API efficiency rule

Prefer one combined/batched API query over many small requests when MediaWiki supports it. Large list-style results should also be iterable so callers do not have to materialize complete datasets. The client must remain serial, respect server backoff signals and make cache reuse visible through statistics.
