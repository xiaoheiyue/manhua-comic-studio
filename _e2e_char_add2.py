import requests
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/project/load",json={"file":"e99af31721.json"},timeout=30); print("load",r.status_code)
r=requests.post(BASE+"/api/character/add",json={"name":"检测新角色"},timeout=30); print("add",r.status_code)
p=r.json()["project"]; c=next(c for c in p["characters"] if c["name"]=="检测新角色"); print("count",len(p["characters"]))
r=requests.post(BASE+"/api/character/delete",json={"id":c["id"]},timeout=30); print("delete",r.status_code,len(r.json()["project"]["characters"]))
r=requests.post(BASE+"/api/character/delete",json={"id":c["id"]},timeout=30); print("delete again",r.status_code,r.text[:80])
r=requests.post(BASE+"/api/project/load",json={"file":"9a88d9b8ce.json"},timeout=30); print("reload final",r.status_code)
