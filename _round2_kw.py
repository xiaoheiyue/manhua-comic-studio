import requests
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
for c in p["characters"]:
    print(c["name"],c["keywords"])
