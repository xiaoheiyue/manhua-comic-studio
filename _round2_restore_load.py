import requests, json
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/project/load",json={"file":"558f643800_good.json"},timeout=60); print("load",r.status_code)
p=requests.get(BASE+"/api/project").json(); print(p["id"],p["title"],len(p["shots"]),len(p["pages"]))
for pg in p["pages"]: print(pg["index"],pg["layout_id"],pg["panels"],pg["shot_indices"],pg["status"])
r=requests.post(BASE+"/api/page/compose",json={"force":True},timeout=180); print("compose",r.status_code,r.text[:160])
p=requests.get(BASE+"/api/project").json()
for pg in p["pages"]: print(pg["index"],pg["layout_id"],pg["status"],pg["image_path"])
