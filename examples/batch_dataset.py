from mtgwiki import Wiki, write_jsonl

wiki = Wiki(cache_path=".mtgwiki-cache.sqlite", cache_ttl=24 * 60 * 60)

snapshots = wiki.extract_many(
    [
        "Urza",
        "Dack Fayden",
        "Skyship Weatherlight",
        "Bloomburrow (plane)",
    ]
)

write_jsonl("pages.jsonl", snapshots)
print(wiki.client.stats())
