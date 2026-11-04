import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
t0=time.time()
r=requests.post(BASE+"/api/analyze",json={"text":p["novel_text"],"title":p["title"]},timeout=30)
print("start",r.status_code,r.text[:120])
job=r.json()["job_id"]
while time.time()-t0<300:
    time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json()
out={"summary":p["summary"],"plotline":p["plotline"],"characters":[{"name":c["name"],"age":c["age"],"identity":c["identity"],"role":c["role"],"is_extra":c["is_extra"],"keywords":c["keywords"],"design":c["design"]} for c in p["characters"]],"scenes":p["scenes"],"style_id":p.get("style_id"),"style":p.get("style")}
pathlib.Path("outputs/_round2_analysis.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print("chars",len(p["characters"]),"scenes",len(p["scenes"]),"style",p.get("style_id"))
for c in p["characters"]: print(c["name"],c["age"],c["role"],c["is_extra"])
