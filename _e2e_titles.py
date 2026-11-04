import requests
ps=requests.get("http://127.0.0.1:7870/api/project/list",timeout=30).json()["projects"]
for p in ps[:8]: print(p["id"],repr(p["title"]))
