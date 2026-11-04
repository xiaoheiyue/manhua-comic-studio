import json, requests, time, pathlib
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/project/load",json={"file":"9a88d9b8ce.json"},timeout=60); print("load",r.status_code)
r=requests.post(BASE+"/api/storyboard/grid25",json={},timeout=30); print("grid",r.status_code,r.text[:120])
job=r.json()["job_id"]; t0=time.time()
while time.time()-t0<240:
    time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("job",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json()
print("grid status",p.get("grid25_status"),"image",p.get("grid25_image"),"shots",p.get("grid25_shots"))
if p.get("grid25_image"):
    pathlib.Path("outputs/_e2e_grid25.png").write_bytes(requests.get(BASE+"/api/image",params={"path":p["grid25_image"]},timeout=30).content)
