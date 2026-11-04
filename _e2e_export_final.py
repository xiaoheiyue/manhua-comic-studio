import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json(); pg=p["pages"][1]
r=requests.post(BASE+"/api/page/set-layout",json={"page_id":pg["id"],"layout_id":"SB-001"},timeout=30); print("set",r.status_code)
p=requests.get(BASE+"/api/project").json(); pg=p["pages"][1]
r=requests.post(BASE+"/api/page/compose",json={"page_id":pg["id"],"force":True},timeout=60); print("compose",r.status_code,r.text[:100])
t0=time.time(); r=requests.post(BASE+"/api/export",json={"folder_name":"孔乙己_最后的酒_最终版","reader":True,"zip":True},timeout=30); print("export",r.status_code)
job=r.json()["job_id"]
while time.time()-t0<300:
    time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
pathlib.Path("outputs/_e2e_final_export.json").write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding="utf-8")
print((j.get("result") or {}).get("output_dir"))
