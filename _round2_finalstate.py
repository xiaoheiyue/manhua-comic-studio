import requests, json, pathlib
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
print("title",p["title"],"shots",len(p["shots"]),"pages",len(p["pages"]))
for s in p["shots"]:
    print(s["index"],s["status"],s["quality_score"],s["characters"],s["panel_size"],s.get("bubble_count") if "bubble_count" in s else "")
for pg in p["pages"]:
    print("page",pg["index"],pg["layout_id"],pg["panels"],pg["shot_indices"],pg["status"],pg.get("image_path"))
pathlib.Path("outputs/_round2_final_state.json").write_text(json.dumps({"id":p["id"],"shots":[{k:s.get(k) for k in ("index","status","quality_score","characters","panel_size","image_path")} for s in p["shots"]],"pages":[{k:pg.get(k) for k in ("index","layout_id","layout_name","panels","shot_indices","status","image_path")} for pg in p["pages"]]},ensure_ascii=False,indent=2),encoding="utf-8")
