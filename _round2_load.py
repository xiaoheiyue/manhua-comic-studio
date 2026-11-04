import requests, json
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/project/load",json={"file":"558f643800.json"},timeout=30); print("load",r.status_code)
p=requests.get(BASE+"/api/project").json(); print("id",p["id"],"title",p["title"],"chars",len(p["characters"]),"shots",len(p["shots"]))
for c in p["characters"]:
    print(c["name"],"design",len(c.get("design") or ""),"main",bool(c.get("main_visual")),"status",c.get("image_status"))
