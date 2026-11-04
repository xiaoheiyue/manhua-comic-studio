import json, requests, pathlib, time
BASE="http://127.0.0.1:7870"
t0=time.time()
r=requests.post(BASE+"/api/storyboard/decompose", json={}, timeout=30)
print("start",r.status_code,r.text[:160])
job=r.json()["job_id"]
while time.time()-t0<420:
    time.sleep(2)
    j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running":
        print("state",j.get("state"),"msg",j.get("message"),"err",j.get("error"),"sec",round(time.time()-t0,1))
        break
p=requests.get(BASE+"/api/project").json()
shots=p["shots"]
print("shots",len(shots),"pages",len(p.get("pages") or []))
from collections import Counter
print("shot_types",Counter(s.get("shot_type") for s in shots))
print("beats",Counter(s.get("beat") for s in shots))
print("dialogues",sum(len(s.get("dialogues") or []) for s in shots))
out={"count":len(shots),"pages":p.get("pages"),"shots":[{k:s.get(k) for k in ("index","shot_type","camera","composition","lighting","panel_scale","beat","pacing","panel_emphasis","characters","scene","description","action","expression","dialogues","silent","hook")} for s in shots]}
pathlib.Path("outputs/_e2e_storyboard_real.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
