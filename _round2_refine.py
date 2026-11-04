import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
t0=time.time()
r=requests.post(BASE+"/api/refine/start",json={},timeout=30); print("start",r.status_code,r.text[:120])
job=r.json()["job_id"]
while time.time()-t0<300:
    time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json()
out={"core_task":p.get("core_task"),"anchors":p.get("visual_anchors"),"cells":p.get("continuous_cells"),"status":p.get("refine_status")}
pathlib.Path("outputs/_round2_refine.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print("anchors",len(p.get("visual_anchors") or []),"cells",len(p.get("continuous_cells") or []),"status",p.get("refine_status"))
