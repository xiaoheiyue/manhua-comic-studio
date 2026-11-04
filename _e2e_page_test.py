import json, requests, time, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
pg=p["pages"][1]
print("page",pg["index"],pg["id"],pg["layout_id"],pg["panels"])
r=requests.post(BASE+"/api/page/set-layout",json={"page_id":pg["id"],"layout_id":"SB-001"},timeout=30)
print("set",r.status_code,r.text[:150])
r=requests.post(BASE+"/api/page/compose",json={"page_id":pg["id"],"force":True},timeout=120)
print("compose",r.status_code,r.text[:200])
p=requests.get(BASE+"/api/project").json(); pg2=p["pages"][1]
print("now",pg2["layout_id"],pg2["status"],pg2["image_path"])
# 复制一份换版后的页图供报告对比
import shutil
shutil.copy(pg2["image_path"],"outputs/_e2e_page2_SB001.png")
# 恢复原版式
r=requests.post(BASE+"/api/page/set-layout",json={"page_id":pg["id"],"layout_id":"SB-009"},timeout=30)
print("restore",r.status_code,r.text[:120])
r=requests.post(BASE+"/api/page/compose",json={"page_id":pg["id"],"force":True},timeout=120)
print("recompose",r.status_code,r.text[:150])
p=requests.get(BASE+"/api/project").json(); pg3=p["pages"][1]
print("final",pg3["layout_id"],pg3["status"])
