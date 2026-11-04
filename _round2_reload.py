import requests
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/project/load",json={"file":"558f643800.json"},timeout=30); print("load",r.status_code)
p=requests.get(BASE+"/api/project").json(); print(p["id"],p["title"],len(p["shots"]),len(p["pages"]))
