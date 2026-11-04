import json, requests, shutil, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json(); pg=p["pages"][1]
print("current page",pg["index"],pg["id"],pg["layout_id"],pg["status"])
r=requests.post(BASE+"/api/page/compose",json={"page_id":pg["id"],"force":True},timeout=120)
print("compose",r.status_code,r.text[:150])
p=requests.get(BASE+"/api/project").json(); pg2=p["pages"][1]
print("now",pg2["id"],pg2["layout_id"],pg2["status"],pg2["image_path"])
if pg2.get("image_path"):
    shutil.copy(pg2["image_path"],"outputs/_e2e_page2_SB001.png")
# 恢复 SB-009
r=requests.post(BASE+"/api/page/set-layout",json={"page_id":pg2["id"],"layout_id":"SB-009"},timeout=30)
print("restore",r.status_code)
p=requests.get(BASE+"/api/project").json(); pg3=p["pages"][1]
r=requests.post(BASE+"/api/page/compose",json={"page_id":pg3["id"],"force":True},timeout=120)
print("recompose",r.status_code,r.text[:150])
p=requests.get(BASE+"/api/project").json(); pg4=p["pages"][1]
print("final",pg4["layout_id"],pg4["status"],pg4["image_path"])
