import requests, json, time, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
shots=[]
for s in p["shots"]:
    item={k:s.get(k) for k in ("id","shot_type","characters","scene","description","mood","source_text","camera","composition","lighting","transition","panel_scale","focus","action","expression","time_of_day","beat","pacing","panel_emphasis","caption","char_sides","silent","hook")}
    ds=s.get("dialogues") or []
    if s["index"]==9 and len(ds)>1:
        # 只保留第一条，去掉重复台词
        ds=[ds[0]]
    item["dialogues"]=ds
    shots.append(item)
r=requests.post(BASE+"/api/storyboard/save",json={"shots":shots},timeout=60)
print("save",r.status_code,r.text[:120])
p=requests.get(BASE+"/api/project").json()
s9=next(s for s in p["shots"] if s["index"]==9)
print("shot9 dialogues",json.dumps([{k:d.get(k) for k in ("speaker","text","kind")} for d in s9["dialogues"]],ensure_ascii=False))
print("shots",len(p["shots"]),"pages",len(p["pages"]))
# 重生成第9格
t0=time.time(); r=requests.post(BASE+"/api/generate/single",json={"id":s9["id"]},timeout=30)
job=r.json()["job_id"]
while time.time()-t0<300:
    time.sleep(3); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("regen",j.get("state"),j.get("message"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json(); s=next(x for x in p["shots"] if x["index"]==9)
print("shot9",s["status"],s["quality_score"],len(s.get("bubble_layout") or []))
