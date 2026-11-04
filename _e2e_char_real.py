import json, requests, time, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
c=next(c for c in p["characters"] if c["name"]=="孔乙己")
print("cid",c["id"],"design?",bool(c.get("design")))
# 人设
r=requests.post(BASE+"/api/character/design",json={"id":c["id"]},timeout=30)
print("design",r.status_code)
job=r.json().get("job_id")
while job:
    time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("design job",j.get("state"),j.get("error")); break
t0=time.time()
r=requests.post(BASE+"/api/character/images",json={"id":c["id"]},timeout=30)
print("images",r.status_code,r.text[:120])
job=r.json().get("job_id")
while time.time()-t0<900:
    time.sleep(3); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("images job",j.get("state"),j.get("message"),"err",j.get("error"),"sec",round(time.time()-t0,1)); break
c=next(c for c in requests.get(BASE+"/api/project").json()["characters"] if c["name"]=="孔乙己")
print(json.dumps({k:c.get(k) for k in ("name","design","keywords","main_visual","pose_sheet","triple_view","image_status","pose_status","error")},ensure_ascii=False,indent=2)[:1200])
