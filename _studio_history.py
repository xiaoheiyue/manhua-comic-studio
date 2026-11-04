import requests
BASE="http://127.0.0.1:7870"
r=requests.get(BASE+"/api/studio/history",timeout=30); print("history",r.status_code,"items",len(r.json().get("items") or []))
for x in (r.json().get("items") or [])[:8]: print(x.get("mode"),x.get("image"))
r=requests.get(BASE+"/api/studio/catalog",timeout=30); d=r.json(); print("modes",len(d["modes"]),"styles",len(d["styles"]),"colors",len(d["colors"]))
