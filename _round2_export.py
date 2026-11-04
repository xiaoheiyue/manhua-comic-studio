import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
t0=time.time()
r=requests.post(BASE+"/api/export",json={"folder_name":"孔乙己_一致性优化_第二轮","reader":True,"zip":True},timeout=30)
print("export",r.status_code,r.text[:120])
job=r.json()["job_id"]
while time.time()-t0<300:
    time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
res=j.get("result") or {}; print(res.get("output_dir"),res.get("zip_path"))
pathlib.Path("outputs/_round2_export.json").write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding="utf-8")
