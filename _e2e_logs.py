import requests
r=requests.get("http://127.0.0.1:7870/api/logs",timeout=10); print(r.status_code)
d=r.json(); txt=d.get("logs") or ""
print("chars",len(txt),"lines",len(txt.splitlines()))
print("\n".join(txt.splitlines()[-8:]))
