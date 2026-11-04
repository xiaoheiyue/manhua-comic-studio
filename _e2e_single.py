import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
requests.post(BASE+"/api/project/load",json={"file":"9a88d9b8ce.json"},timeout=60)
p=requests.get(BASE+"/api/project").json(); s9=next(s for s in p["shots"] if s["index"]==9); s4=next(s for s in p["shots"] if s["index"]==4)
r=requests.post(BASE+"/api/shot/skip",json={"id":s9["id"],"skip":True},timeout=30); print("skip",r.status_code,r.json().get("message"),r.json()["shot"]["status"])
r=requests.post(BASE+"/api/shot/skip",json={"id":s9["id"],"skip":False},timeout=30); print("unskip",r.status_code,r.json().get("message"),r.json()["shot"]["status"])
r=requests.post(BASE+"/api/shot/panel-size",json={"id":s4["id"],"height":1400},timeout=30); print("size",r.status_code,r.json().get("message"))
r=requests.post(BASE+"/api/shot/panel-size",json={"id":s4["id"],"height":0},timeout=30); print("size reset",r.status_code,r.json().get("message"))
# 单格重生成
t0=time.time()
r=requests.post(BASE+"/api/generate/single",json={"id":s4["id"]},timeout=30)
print("single start",r.status_code,r.text[:100])
job=r.json()["job_id"]
while time.time()-t0<600:
    time.sleep(3); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running":
        print("single",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json(); s=next(x for x in p["shots"] if x["index"]==4)
print("shot4",s["status"],s["quality_score"],s["panel_size"])
pathlib.Path("outputs/_e2e_single.json").write_text(json.dumps({"shot":s,"skip9":next(x for x in p["shots"] if x["index"]==9)["status"]},ensure_ascii=False,indent=2),encoding="utf-8")
