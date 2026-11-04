import requests, pathlib
BASE="http://127.0.0.1:7870"
text="雨夜，老宅的木门被风推开。\n\n“你终于来了。”沈砚说。"
p=pathlib.Path("outputs/_round2_upload_utf8.txt"); p.write_text(text,encoding="utf-8")
p2=pathlib.Path("outputs/_round2_upload_gbk.txt"); p2.write_bytes(text.encode("gbk"))
for path in (p,p2):
    with path.open("rb") as f:
        r=requests.post(BASE+"/api/text/upload",files={"file":(path.name,f,"text/plain")},timeout=30)
    d=r.json(); print(path.name,r.status_code,d.get("encoding"),d.get("length"),d.get("replacement_chars"))
