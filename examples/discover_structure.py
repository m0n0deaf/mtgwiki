from mtgwiki import Wiki

wiki = Wiki()

structure = wiki.structure(
    "Skyship Weatherlight",
    section="In-game references",
)

for template in structure["templates"]:
    print(f"{template['name']}  depth={template['depth']}")
    for name, raw_value in template["params"].items():
        print(f"  {name} = {raw_value[:100]!r}")
