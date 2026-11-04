import requests,json
d=requests.get("http://127.0.0.1:7870/api/studio/catalog",timeout=10).json()
for m in d["modes"]:
    print(json.dumps(m,ensure_ascii=False))
