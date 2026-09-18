from mtgwiki import Wiki

wiki = Wiki()

# Generic MediaWiki category traversal. The library handles continuation,
# subcategory recursion, cycles and duplicate pages.
for member in wiki.iter_members(
    "Planeswalker characters",
    recurse=True,
    namespace=0,
    cmtype="page",
):
    print(member["title"])
