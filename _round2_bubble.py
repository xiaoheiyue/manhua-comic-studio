import requests, json, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json(); s=next(x for x in p["shots"] if x["index"]==5)
r=requests.get(BASE+"/api/bubble/layout",params={"shot_id":s["id"]},timeout=30); print("layout",r.status_code,len(r.json().get("layout") or []))
lay=r.json().get("layout") or []
if lay:
    lay=[dict(x) for x in lay]; lay[0]["x"]=max(0.02,min(0.60,float(lay[0]["x"])-0.03))
    r2=requests.post(BASE+"/api/bubble/preview",json={"id":s["id"],"layout":lay},timeout=60)
    print("preview",r2.status_code,r2.text[:120])
    if r2.ok:
        path=r2.json()["preview_path"]; pathlib.Path("outputs/_round2_bubble_preview.png").write_bytes(requests.get(BASE+"/api/image",params={"path":path},timeout=30).content)
