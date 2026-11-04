import json, requests, time, pathlib
BASE="http://127.0.0.1:7870"
log=pathlib.Path("outputs/_e2e_auto_progress.log")
r=requests.post(BASE+"/api/auto/start",json={"export":True},timeout=30)
print("start",r.status_code,r.text[:200],flush=True)
job=r.json()["job_id"]
t0=time.time(); last=""
while time.time()-t0<2400:
    time.sleep(5)
    try:
        j=requests.get(f"{BASE}/api/job/{job}",timeout=15).json()
    except Exception as e:
        print("poll error",e,flush=True); continue
    msg=f"{round(time.time()-t0)}s progress={j.get('progress'):.3f} state={j.get('state')} msg={j.get('message')}"
    if msg!=last:
        print(msg,flush=True); log.write_text(msg+"\n",encoding="utf-8"); last=msg
    if j.get("state")!="running":
        print("FINAL",j.get("state"),j.get("error"),flush=True)
        pathlib.Path("outputs/_e2e_auto_result.json").write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding="utf-8")
        break
else:
    print("TIMEOUT",flush=True)
