import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
ref=json.load(open("outputs/_studio_ref_upload.json",encoding="utf-8"))["path"]
payload={"mode":"emoji","topic":"同一只戴红围巾的橘猫的九种新表情","style_id":"FA-005","color_ids":["C-12"],
         "emoji_grid":3,"emoji_text_mode":"overlay",
         "emoji_texts":["开心","困","饿","生气","疑惑","得意","哭","害羞","打招呼"],
         "reference_image":ref,"reference_mode":"identity","force":True}
t0=time.time(); r=requests.post(BASE+"/api/studio/generate",json=payload,timeout=30)
print("start",r.status_code,r.text[:120])
job=r.json()["job_id"]
while time.time()-t0<900:
    time.sleep(3); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if int(time.time()-t0)%30<4: print(round(time.time()-t0),j.get("message"),flush=True)
    if j.get("state")!="running": print("done",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1)); break
pathlib.Path("outputs/_studio_emoji_ref.json").write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding="utf-8")
res=j.get("result") or {}; meta=res.get("meta") or {}
print("image",meta.get("image"),"stickers",len(meta.get("stickers") or []),"refmode",meta.get("reference_mode"))
