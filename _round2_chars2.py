import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
t0=time.time()
r=requests.post(BASE+"/api/character/all-images",json={},timeout=30)
print("start",r.status_code,r.text[:120])
job=r.json()["job_id"]
while time.time()-t0<1800:
    time.sleep(5); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if int(time.time()-t0)%30<6: print(round(time.time()-t0),j.get("progress"),j.get("message"),flush=True)
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json()
out={"characters":[{"name":c["name"],"age":c["age"],"design":c["design"],"keywords":c["keywords"],"main_visual":c["main_visual"],"pose_sheet":c["pose_sheet"],"image_status":c["image_status"],"seed":c.get("seed")} for c in p["characters"]]}
pathlib.Path("outputs/_round2_characters2.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
for c in p["characters"]: print(c["name"],c["image_status"],c.get("main_visual"))
