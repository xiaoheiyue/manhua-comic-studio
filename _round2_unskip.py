import requests
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json(); s=next(x for x in p["shots"] if x["index"]==9)
print("before",s["status"],bool(s["image_path"]))
r=requests.post(BASE+"/api/shot/skip",json={"id":s["id"],"skip":True},timeout=30); print("skip",r.json()["shot"]["status"])
r=requests.post(BASE+"/api/shot/skip",json={"id":s["id"],"skip":False},timeout=30); print("unskip",r.json()["shot"]["status"],bool(r.json()["shot"]["image_path"]))
