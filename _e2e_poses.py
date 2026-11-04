import json, requests, pathlib
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/storyboard/poses25",timeout=60)
print("poses",r.status_code)
d=r.json(); print("msg",d.get("message"))
print(json.dumps(d.get("items"),ensure_ascii=False,indent=2)[:1200])
p=requests.get(BASE+"/api/project").json()
print("pose status",p.get("pose_status"),"shots with skeleton",sum(1 for s in p["shots"] if s.get("pose_skeleton")))
pathlib.Path("outputs/_e2e_poses.json").write_text(json.dumps(d.get("items"),ensure_ascii=False,indent=2),encoding="utf-8")
