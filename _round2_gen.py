import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
t0=time.time()
r=requests.post(BASE+"/api/generate/start",json={"force":True,"bubbles":True},timeout=30)
print("start",r.status_code,r.text[:120])
job=r.json()["job_id"]
while time.time()-t0<1200:
    time.sleep(5); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if int(time.time()-t0)%30<6: print(round(time.time()-t0),j.get("progress"),j.get("message"),flush=True)
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json()
pathlib.Path("outputs/_round2_gen.json").write_text(json.dumps({"shots":[{k:s.get(k) for k in ("index","status","quality_score","image_path","raw_image_path","characters","panel_size","error")} for s in p["shots"]]},ensure_ascii=False,indent=2),encoding="utf-8")
for s in p["shots"]: print(s["index"],s["status"],s.get("quality_score"),s["characters"],s.get("error"))
