import requests, json, pathlib
BASE="http://127.0.0.1:7870"
r=requests.get(BASE+"/api/export/list",timeout=30); print("list",r.status_code)
d=r.json(); print("folders",len(d.get("folders") or []),"zips",len(d.get("zips") or []))
print(json.dumps(d.get("folders",[])[:2],ensure_ascii=False,indent=2)[:800])
print(json.dumps(d.get("zips",[])[:2],ensure_ascii=False,indent=2)[:500])
# 下载压缩包（只取头部，验证接口）
z=(d.get("zips") or [{}])[0]
path=z.get("path") or z.get("file") or z.get("name")
print("zip entry",z)
if path:
    rr=requests.get(BASE+"/api/download",params={"path":path},timeout=60,stream=True)
    print("download",rr.status_code,rr.headers.get("content-length"),rr.headers.get("content-type"))
    chunk=next(rr.iter_content(1024),b""); print("first bytes",len(chunk),chunk[:4])
