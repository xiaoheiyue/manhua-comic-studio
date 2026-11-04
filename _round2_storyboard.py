import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
t0=time.time()
r=requests.post(BASE+"/api/storyboard/decompose",json={},timeout=30)
print("start",r.status_code,r.text[:120])
job=r.json()["job_id"]
while time.time()-t0<600:
    time.sleep(3); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json()
print("shots",len(p["shots"]),"pages",len(p.get("pages") or []))
for s in p["shots"]:
    print(s["index"],s["shot_type"],s["characters"],"dlg",len(s.get("dialogues") or []))
pathlib.Path("outputs/_round2_storyboard.json").write_text(json.dumps({"shots":[{k:s.get(k) for k in ("index","shot_type","characters","scene","description","dialogues","panel_scale","beat","quality_score")} for s in p["shots"]],"pages":[{k:pg.get(k) for k in ("index","layout_id","layout_name","panels","shot_indices")} for pg in (p.get("pages") or [])]},ensure_ascii=False,indent=2),encoding="utf-8")
