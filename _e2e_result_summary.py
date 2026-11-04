import json, pathlib
j=json.load(open("outputs/_e2e_auto_result.json",encoding="utf-8"))
p=j["result"]
for k in ("id","title","tagline","chapter_no"):
    print(k,"=",p.get(k))
print("shots",len(p["shots"]),"pages",len(p.get("pages") or []))
print("output_dir",p.get("output_dir"),"zip",p.get("zip_path"))
print("meta", {k:p.get(k) for k in ("core_task","refine_status","style_id","style","style_rationale","tone")})
for s in p["shots"]:
    print(s["index"],s.get("status"),s.get("quality_score"),s.get("image"),s.get("panel_size"),s.get("error"))
for pg in p.get("pages") or []:
    print("page",pg.get("index"),pg.get("layout_id"),pg.get("status"),pg.get("image_path"))
