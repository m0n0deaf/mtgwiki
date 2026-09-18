from mtgwiki import Wiki

wiki = Wiki()

snapshot = wiki.extract("Skyship Weatherlight")

print(snapshot["page"])
print("Templates discovered:", len(snapshot["structure"]["templates"]))
print("Links discovered:", len(snapshot["structure"]["wikilinks"]))
print("API stats:", wiki.client.stats())
