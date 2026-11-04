import requests
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
print("id",p["id"],"shots",len(p["shots"]))
for c in p["characters"]:
    print(c["name"],c["image_status"],bool(c.get("main_visual")),c.get("seed"))
