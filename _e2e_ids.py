import json, requests, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
for s in p["shots"]:
    print(s["index"],s["id"],s["status"],len(s.get("dialogues") or []),len(s.get("bubble_layout") or []),s.get("raw_image_path") and "raw")
pathlib.Path("outputs/_e2e_shots_ids.json").write_text(json.dumps([{k:s.get(k) for k in ("index","id","status","dialogues","bubble_layout","raw_image_path","image_path")} for s in p["shots"]],ensure_ascii=False,indent=2),encoding="utf-8")
