import json, requests, pathlib
BASE="http://127.0.0.1:7870"
j=json.load(open("outputs/_studio_emoji_ref.json",encoding="utf-8"))
meta=(j.get("result") or {}).get("meta") or {}
img=meta.get("image")
r=requests.get(BASE+"/api/studio/stickers",params={"image":img},timeout=120)
print("stickers",r.status_code,len(r.content),r.headers.get("content-type"))
out=pathlib.Path("outputs/_studio_emoji_stickers.zip"); out.write_bytes(r.content)
import zipfile
with zipfile.ZipFile(out) as z:
    print("entries",len(z.namelist()),z.namelist()[:5])
