import json
p=json.load(open("outputs/_round2_storyboard.json",encoding="utf-8"))
for s in p["shots"]:
    print(f'#{s["index"]} {s["shot_type"]}/{s["panel_scale"]} {s["beat"]} {s["characters"]}')
    print("  ",s["description"][:120])
    for d in s["dialogues"]: print("   dlg",d.get("speaker"),d.get("text"))
