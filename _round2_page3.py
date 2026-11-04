import requests
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json(); pg=p["pages"][2]
print("before",pg["index"],pg["id"],pg["layout_id"],pg["panels"])
r=requests.post(BASE+"/api/page/set-layout",json={"page_id":pg["id"],"layout_id":"SB-001"},timeout=30); print("set",r.status_code)
p=requests.get(BASE+"/api/project").json(); pg=p["pages"][2]
r=requests.post(BASE+"/api/page/compose",json={"page_id":pg["id"],"force":True},timeout=90); print("compose",r.status_code,r.text[:120])
p=requests.get(BASE+"/api/project").json(); pg=p["pages"][2]
print("after",pg["layout_id"],pg["status"],pg["image_path"])
