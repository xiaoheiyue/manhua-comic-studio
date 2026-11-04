import requests, pathlib, json
BASE="http://127.0.0.1:7870"
p=pathlib.Path("outputs/_studio_ref_cat.png")
with p.open("rb") as f:
    r=requests.post(BASE+"/api/studio/upload-ref",files={"file":("cat_ref.png",f,"image/png")},timeout=180)
print("upload",r.status_code,r.text[:300])
if r.ok:
    json.dump(r.json(),open("outputs/_studio_ref_upload.json","w",encoding="utf-8"),ensure_ascii=False,indent=2)
