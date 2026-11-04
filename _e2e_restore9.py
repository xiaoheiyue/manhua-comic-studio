import requests, time, json
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json(); s9=next(s for s in p["shots"] if s["index"]==9)
print("before",s9["status"],s9["skip"])
t0=time.time(); r=requests.post(BASE+"/api/generate/single",json={"id":s9["id"]},timeout=30)
print("start",r.status_code)
job=r.json()["job_id"]
while time.time()-t0<300:
    time.sleep(3); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json(); s=next(x for x in p["shots"] if x["index"]==9)
print("after",s["status"],s["skip"],s["quality_score"])
