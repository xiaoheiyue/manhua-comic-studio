import requests
d=requests.get("http://127.0.0.1:7870/api/style/list",timeout=30).json()
for s in d["styles"]:
    if s["id"] in ("FE-061","FA-018","FA-005","FE-053","FG-023"):
        print(s["id"],s.get("name"),s.get("name_zh") or s.get("name_cn"),s.get("group"))
