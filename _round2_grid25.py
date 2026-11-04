import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
t0=time.time(); r=requests.post(BASE+"/api/storyboard/grid25",json={},timeout=30); print("grid",r.status_code)
job=r.json()["job_id"]
while time.time()-t0<600:
    time.sleep(3); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if int(time.time()-t0)%30<4: print(round(time.time()-t0),j.get("message"),flush=True)
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),round(time.time()-t0,1)); break
r=requests.post(BASE+"/api/storyboard/poses25",timeout=60); print("poses",r.status_code,r.json().get("message"))
p=requests.get(BASE+"/api/project").json(); print("grid",p.get("grid25_status"),"pose skeletons",sum(1 for s in p["shots"] if s.get("pose_skeleton")))
pathlib.Path("outputs/_round2_gridposes.json").write_text(json.dumps({"grid":p.get("grid25_image"),"items":r.json().get("items")},ensure_ascii=False,indent=2),encoding="utf-8")
