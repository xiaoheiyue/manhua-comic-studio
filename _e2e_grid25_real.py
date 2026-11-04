import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
t0=time.time(); r=requests.post(BASE+"/api/storyboard/grid25",json={},timeout=30); print("start",r.status_code,r.text[:100])
job=r.json()["job_id"]
while time.time()-t0<900:
    time.sleep(3); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
    if int(time.time()-t0)%30<4: print(round(time.time()-t0),j.get("message"),flush=True)
p=requests.get(BASE+"/api/project").json(); print("grid",p.get("grid25_status"),p.get("grid25_image"))
if p.get("grid25_image"):
    pathlib.Path("outputs/_e2e_grid25_real.png").write_bytes(requests.get(BASE+"/api/image",params={"path":p["grid25_image"]},timeout=30).content)
