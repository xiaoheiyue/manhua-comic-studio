import json, requests, pathlib, time
BASE="http://127.0.0.1:7870"
text=pathlib.Path("data/uploads/孔乙己_正文.txt").read_text(encoding="utf-8")
r=requests.post(BASE+"/api/project/new", json={"title":"孔乙己·咸亨酒店（真机检测）","text":text}, timeout=30)
p=r.json(); print("project", p["id"], len(p["novel_text"]))
t0=time.time()
r=requests.post(BASE+"/api/analyze", json={"text":text,"title":"孔乙己·咸亨酒店（真机检测）"}, timeout=30)
print("analyze", r.status_code, r.text[:160])
job=r.json()["job_id"]
while time.time()-t0 < 300:
    time.sleep(2)
    j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running":
        print("state",j.get("state"),"msg",j.get("message"),"err",j.get("error"),"sec",round(time.time()-t0,1))
        break
p=requests.get(BASE+"/api/project").json()
out={"id":p["id"],"title":p["title"],"summary":p["summary"],"plotline":p["plotline"],
 "key_points":p.get("key_points"),"style":p["style"],"style_detail":p["style_detail"],
 "characters":[{k:c.get(k) for k in ("name","gender","age","identity","personality","role","is_extra")} for c in p["characters"]],
 "scenes":[{k:s.get(k) for k in ("name","features","atmosphere","frequency")} for s in p["scenes"]],
 "style_id":p.get("style_id"),"style_colors":p.get("style_colors"),"tone":p.get("tone"),"style_auto":p.get("style_auto")}
pathlib.Path("outputs/_e2e_analysis_real.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print("chars",len(p["characters"]),"scenes",len(p["scenes"]),"style",p.get("style_id"))
