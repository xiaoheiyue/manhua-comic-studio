import json
p=json.load(open("data/projects/558f643800.json",encoding="utf-8"))
print("saved chars:")
for c in p["characters"]:
    print(c["name"],c["image_status"],bool(c.get("main_visual")),c.get("seed"))
