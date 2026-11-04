import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
def wait(job,timeout=120):
    t0=time.time()
    while time.time()-t0<timeout:
        time.sleep(0.5); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
        if j.get("state")!="running": return j
    return {"state":"timeout"}
sample=requests.get(BASE+"/api/sample",timeout=10).json()["text"]
requests.post(BASE+"/api/project/new",json={"title":"并发与控制测试","text":sample},timeout=30)
r=requests.post(BASE+"/api/analyze",json={"text":sample},timeout=30); print("analyze",wait(r.json()["job_id"]).get("state"))
r=requests.post(BASE+"/api/storyboard/decompose",json={},timeout=30); j=wait(r.json()["job_id"],180); print("decompose",j.get("state"),j.get("message"))
print("shots",len(requests.get(BASE+"/api/project").json()["shots"]))
# 启动生成并立即并发请求
r=requests.post(BASE+"/api/generate/start",json={},timeout=30); job=r.json()["job_id"]; print("gen start",r.status_code)
time.sleep(0.8)
r2=requests.post(BASE+"/api/analyze",json={"text":sample},timeout=30); print("busy analyze",r2.status_code,r2.text[:100])
r3=requests.post(BASE+"/api/generate/pause",timeout=10); print("pause",r3.status_code,r3.text[:80])
time.sleep(0.5)
r4=requests.post(BASE+"/api/generate/resume",timeout=10); print("resume",r4.status_code,r4.text[:80])
time.sleep(0.5)
r5=requests.post(BASE+"/api/generate/cancel",timeout=10); print("cancel",r5.status_code,r5.text[:80])
j=wait(job,60); print("gen final",j.get("state"),j.get("message"),j.get("error"))
p=requests.get(BASE+"/api/project").json(); print("done",sum(1 for s in p["shots"] if s["status"]=="已完成"),"/",len(p["shots"]))
pathlib.Path("outputs/_e2e_control.json").write_text(json.dumps({"shots":len(p["shots"]),"done":sum(1 for s in p["shots"] if s["status"]=="已完成"),"job":j},ensure_ascii=False,indent=2),encoding="utf-8")
