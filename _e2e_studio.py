import json, requests, time, pathlib
BASE="http://127.0.0.1:7870"
payload={"mode":"poster","topic":"秋日午后的一间旧书店，暖黄灯光，木书架与堆叠的旧书，安静怀旧","title":"旧书店的午后","subtitle":"城市里最后一盏不灭的灯","style_id":"FE-061","color_ids":["C-11"],"layout_id":"SC-001","overlay_text":True,"force":True}
r=requests.post(BASE+"/api/studio/generate",json=payload,timeout=30)
print("start",r.status_code,r.text[:160])
job=r.json()["job_id"]; t0=time.time()
while time.time()-t0<600:
    time.sleep(3); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running":
        print("job",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
    if int(time.time()-t0)%30<3: print(round(time.time()-t0),j.get("message"),flush=True)
pathlib.Path("outputs/_e2e_studio_result.json").write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(j.get("result"),ensure_ascii=False,indent=2)[:1500])
