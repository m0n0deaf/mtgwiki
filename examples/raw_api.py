from mtgwiki import Wiki

wiki = Wiki()

result = wiki.api(
    action="query",
    meta="siteinfo",
    siprop="general|namespaces",
)

print(result)
