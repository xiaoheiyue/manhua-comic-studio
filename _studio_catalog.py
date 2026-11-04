import requests, json
d=requests.get("http://127.0.0.1:7870/api/studio/catalog",timeout=30).json()
for m in d["modes"]:
    print(m["id"],m["name"],m["size"],"default",m.get("default_layout"),"layouts",len(m.get("layouts") or []))
    for l in (m.get("layouts") or [])[:3]:
        print("   ",l.get("id"),l.get("name"))
print("styles",len(d["styles"]),"colors",len(d["colors"]))
