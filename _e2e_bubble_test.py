import json, requests, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
s3=next(s for s in p["shots"] if s["index"]==3)
print("layout",json.dumps(s3["bubble_layout"],ensure_ascii=False)[:500])
lay=s3["bubble_layout"]
if lay:
    lay=[dict(x) for x in lay]; lay[0]["x"]=max(0.02,min(0.6,float(lay[0]["x"])+0.05)); lay[0]["y"]=max(0.02,min(0.8,float(lay[0]["y"])+0.03))
r=requests.post(BASE+"/api/bubble/preview",json={"id":s3["id"],"layout":lay},timeout=60)
print("preview",r.status_code,r.text[:200])
if r.ok:
    pth=r.json()["preview_path"]; print("path",pth)
    pathlib.Path("outputs/_e2e_bubble_preview.png").write_bytes(requests.get(BASE+"/api/image",params={"path":pth},timeout=30).content)
# 无气泡格
s1=next(s for s in p["shots"] if s["index"]==1)
r=requests.get(BASE+"/api/bubble/layout",params={"shot_id":s1["id"]},timeout=30)
print("layout1",r.status_code,json.dumps(r.json().get("layout"),ensure_ascii=False)[:300])
