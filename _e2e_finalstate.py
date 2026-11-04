import requests, json
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
print(p["id"],p["title"],len(p["characters"]),len(p["shots"]),len(p.get("pages") or []))
print("grid",p.get("grid25_status"),"shots done",sum(1 for s in p["shots"] if s["status"]=="已完成"))
print("style",p.get("style_id"),p.get("style"))
