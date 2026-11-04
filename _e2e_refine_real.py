import json, requests, time, pathlib
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/project/load",json={"file":"6bd5f2fa0d.json"},timeout=60)
print("load",r.status_code)
r=requests.post(BASE+"/api/refine/start",json={},timeout=30)
print("refine",r.status_code,r.text[:120])
job=r.json()["job_id"]; t0=time.time()
while time.time()-t0<300:
    time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("job",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json()
out={"core_task":p.get("core_task"),"anchors":p.get("visual_anchors"),"cells":p.get("continuous_cells"),"refine_status":p.get("refine_status"),"shots_after":len(p["shots"])}
pathlib.Path("outputs/_e2e_refine_real.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print("core",p.get("core_task"))
print("anchors",len(p.get("visual_anchors") or []),"cells",len(p.get("continuous_cells") or []),"status",p.get("refine_status"),"shots",len(p["shots"]))
