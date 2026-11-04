import requests
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/project/load",json={"file":"558f643800_good.json"},timeout=60); print("load",r.status_code)
r=requests.post(BASE+"/api/page/compose",json={"force":True},timeout=180); print("compose",r.status_code)
