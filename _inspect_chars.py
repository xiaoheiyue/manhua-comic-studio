import json, pathlib, requests
from PIL import Image, ImageDraw, ImageFont
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
print("project",p["id"],p["title"])
for c in p["characters"]:
    print("CHAR",c["name"],"id",c["id"],"extra",c["is_extra"],"design",len(c.get("design") or ""),"main",bool(c.get("main_visual")),"pose",bool(c.get("pose_sheet")))
print("---SHOTS---")
for s in p["shots"]:
    print(s["index"],s["shot_type"],s["characters"],"dlg",len(s.get("dialogues") or []),"score",s.get("quality_score"),"size",s.get("panel_size"))
