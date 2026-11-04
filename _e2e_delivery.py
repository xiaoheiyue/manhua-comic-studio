import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/page/compose",json={"force":True},timeout=180); print("compose",r.status_code,r.text[:120])
t0=time.time(); r=requests.post(BASE+"/api/export",json={"folder_name":"孔乙己_最后的酒_交付版","reader":True,"zip":True},timeout=30); print("export",r.status_code)
job=r.json()["job_id"]
while time.time()-t0<300:
    time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
res=j.get("result") or {}; print(res.get("output_dir"),res.get("zip_path"))
pathlib.Path("outputs/_e2e_delivery.json").write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding="utf-8")
