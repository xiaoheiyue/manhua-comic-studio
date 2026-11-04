import requests, pathlib
BASE="http://127.0.0.1:7870"
r=requests.post(BASE+"/api/storyboard/doc",timeout=30); print("doc",r.status_code,r.text[:160])
if r.ok:
    p=r.json()["file"]; print("exists",pathlib.Path(p).exists(),pathlib.Path(p).stat().st_size)
    rr=requests.get(BASE+"/api/download",params={"path":p},timeout=30); print("download",rr.status_code,len(rr.content),rr.text[:20])
