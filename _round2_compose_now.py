import requests
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/page/compose",json={"force":True},timeout=180)
print(r.status_code,r.text[:160])
p=requests.get(BASE+"/api/project").json()
for pg in p["pages"]: print(pg["index"],pg["layout_id"],pg["status"],pg["image_path"])
