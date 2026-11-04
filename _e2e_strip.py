import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
t0=time.time(); r=requests.post(BASE+"/api/export/strip",json={},timeout=30); print("strip",r.status_code,r.text[:160])
if r.ok and r.json().get("job_id"):
    job=r.json()["job_id"]
    while time.time()-t0<180:
        time.sleep(1); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
        if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); print(json.dumps(j.get("result"),ensure_ascii=False)[:500]); break
