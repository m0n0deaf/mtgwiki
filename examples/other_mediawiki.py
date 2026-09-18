from mtgwiki import Wiki

wiki = Wiki(
    api_url="https://www.mediawiki.org/w/api.php",
    user_agent="example-research/0.1 (https://example.com/contact)",
)

info = wiki.siteinfo(("general", "namespaces"))
print(info["general"]["sitename"])

for item in wiki.iter_list("allpages", limit=5, aplimit=5, apnamespace=0):
    print(item["title"])
