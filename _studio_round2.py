import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
payloads=[
 {"mode":"poster","topic":"深秋午后，咸亨酒店门口，一位穿旧长衫的老人坐在门槛上，光影斑驳","title":"门槛上的酒","subtitle":"孔乙己的最后一碗酒","style_id":"FE-061","color_ids":["C-11","C-12"],"layout_id":"SC-001","overlay_text":True,"force":True},
 {"mode":"social","topic":"读《孔乙己》的三层悲凉：看客的笑、读书人的尊严、消失的十九个钱","title":"看客的笑","subtitle":"重读孔乙己","style_id":"FE-061","color_ids":["C-11"],"layout_id":"SC-001","overlay_text":True,"force":True},
 {"mode":"infographic","topic":"孔乙己人物关系与命运转折：掌柜、酒客、我、丁举人、十九个钱","title":"孔乙己人物关系","subtitle":"谁在笑，谁在欠钱","style_id":"FE-061","color_ids":["C-11"],"layout_id":"IG-001","overlay_text":True,"force":True},
 {"mode":"emoji","topic":"戴红围巾的橘猫，九种日常表情：开心、困、饿、生气、疑惑、得意、哭、害羞、打招呼","style_id":"FA-005","color_ids":["C-12"],"emoji_grid":3,"force":True},
 {"mode":"illustration","topic":"雨夜，老宅门前，穿米色旧风衣的年轻女性撑一把断了伞骨的黑伞，青瓦雨檐，冷灰蓝调","style_id":"FE-061","color_ids":["C-11"],"force":True},
 {"mode":"ip","topic":"一个爱读书的旧书店老板：圆脸、灰布围裙、旧书、温暖灯光、可做头像与三视图","title":"旧书店老板","style_id":"FE-061","color_ids":["C-11"],"layout_id":"IP-001","force":True},
 {"mode":"ecommerce","topic":"手工陶瓷茶杯，暖灰釉色，木桌，晨光，旁边一本旧书","title":"手作茶杯","subtitle":"一只杯子，一段慢时光","style_id":"FE-053","color_ids":["C-12"],"layout_id":"EC-001","overlay_text":True,"force":True},
]
out=[]
for pl in payloads:
    print("START",pl["mode"],flush=True)
    t0=time.time()
    try:
        r=requests.post(BASE+"/api/studio/generate",json=pl,timeout=30)
        if r.status_code!=200:
            print("  HTTP",r.status_code,r.text[:200],flush=True); out.append({"mode":pl["mode"],"ok":False,"error":r.text[:200]}); continue
        job=r.json()["job_id"]
        j={}
        while time.time()-t0<600:
            time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
            if j.get("state")!="running": break
        meta=(j.get("result") or {}).get("meta") or {}
        print("  ",j.get("state"),j.get("message"),j.get("error"),round(time.time()-t0,1),meta.get("image"),flush=True)
        out.append({"mode":pl["mode"],"ok":j.get("state")=="done","seconds":round(time.time()-t0,1),"meta":meta,"items":(j.get("result") or {}).get("items")})
    except Exception as e:
        print("  EXC",e,flush=True); out.append({"mode":pl["mode"],"ok":False,"error":str(e)})
pathlib.Path("outputs/_studio_round2_results.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print("SAVED",len(out))
