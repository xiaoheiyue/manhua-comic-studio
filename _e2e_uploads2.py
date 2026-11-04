import requests, pathlib, json
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
ch=next(c for c in p["characters"] if c["name"]=="孔乙己"); img=pathlib.Path(ch["main_visual"])
r=requests.post(BASE+"/api/character/top-clear",json={"id":ch["id"]},timeout=30); print("top clear",r.status_code,r.text[:90])
s=next(x for x in p["shots"] if x["index"]==2)
with img.open("rb") as f:
    r=requests.post(BASE+"/api/shot/pose-upload",data={"shot_id":s["id"],"convert":"false"},files={"file":("pose.png",f,"image/png")},timeout=60)
print("pose upload",r.status_code,r.text[:120])
r=requests.post(BASE+"/api/shot/pose-clear",json={"shot_id":s["id"]},timeout=30); print("pose clear",r.status_code,r.text[:90])
with img.open("rb") as f:
    r=requests.post(BASE+"/api/shot/prop-upload",data={"shot_id":s["id"]},files={"file":("prop.png",f,"image/png")},timeout=120)
print("prop upload",r.status_code,r.text[:120])
r=requests.post(BASE+"/api/shot/prop-clear",json={"shot_id":s["id"]},timeout=30); print("prop clear",r.status_code,r.text[:90])
