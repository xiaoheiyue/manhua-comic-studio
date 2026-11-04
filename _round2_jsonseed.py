import json, pathlib
p=json.load(open("data/projects/558f643800.json",encoding="utf-8"))
for c in p["characters"]:
    print(c["name"],"seed",c.get("seed"),"main",bool(c.get("main_visual")),"hard",c.get("hard_features"))
