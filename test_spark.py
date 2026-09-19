from mtgwiki import Wiki

wiki = Wiki()

walkers = wiki.members(
    "Planeswalker characters",
    recurse=True,
    namespace=0,
    cmtype="page",
)

print(len(walkers))

pages = wiki.extract_many(
    [x["title"] for x in walkers],
    include=("structure",),
)

print(len(pages))