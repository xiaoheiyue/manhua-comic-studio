import requests, json, time
p=requests.get("http://127.0.0.1:7870/api/project",timeout=15).json()
print("title",p["title"],"shots",len(p["shots"]),"pages",len(p.get("pages") or []))
for c in p["characters"]:
    print(c["name"], "image",c.get("image_status"),"pose",c.get("pose_status"),"main",bool(c.get("main_visual")),"err",c.get("error"))
print("progress",p.get("progress"))
