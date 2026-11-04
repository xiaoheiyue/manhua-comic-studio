import requests, json, pathlib, shutil
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json(); s3=next(s for s in p["shots"] if s["index"]==3)
final=pathlib.Path(s3["image_path"]); backup=pathlib.Path("outputs/_e2e_shot3_before.png")
shutil.copy(final,backup)
r=requests.post(BASE+"/api/bubble/auto",json={"id":s3["id"],"position":"top"},timeout=120)
print("auto",r.status_code,r.text[:250])
if r.ok:
    print("layout",json.dumps(r.json().get("layout"),ensure_ascii=False)[:400])
# 用当前布局保存
r2=requests.post(BASE+"/api/bubble/save",json={"id":s3["id"],"layout":r.json().get("layout")},timeout=120)
print("save",r2.status_code,r2.text[:200])
p2=requests.get(BASE+"/api/project").json(); s=next(x for x in p2["shots"] if x["index"]==3)
print("bubble_count",len(s.get("bubble_layout") or []),"quality",s.get("quality_score"))
shutil.copy(final,"outputs/_e2e_shot3_after.png")
