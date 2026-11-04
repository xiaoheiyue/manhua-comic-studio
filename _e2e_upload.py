import requests, json, pathlib
BASE="http://127.0.0.1:7870"
# 上传 UTF-8 测试
text="雨夜，老宅的木门被风推开。林晚站在门外，手里攥着半张泛黄的相片。\n\n“你终于来了。”沈砚说。"
p=pathlib.Path("outputs/_e2e_upload_test.txt"); p.write_text(text,encoding="utf-8")
with p.open("rb") as f:
    r=requests.post(BASE+"/api/text/upload",files={"file":("测试_utf8.txt",f,"text/plain")},timeout=30)
print("utf8",r.status_code)
d=r.json(); print({k:d.get(k) for k in ("filename","encoding","length","bytes","lines","replacement_chars","complete")})
# GBK 测试
p2=pathlib.Path("outputs/_e2e_upload_gbk.txt"); p2.write_bytes(text.encode("gbk"))
with p2.open("rb") as f:
    r=requests.post(BASE+"/api/text/upload",files={"file":("测试_gbk.txt",f,"text/plain")},timeout=30)
print("gbk",r.status_code)
dd=r.json(); print({k:dd.get(k) for k in ("filename","encoding","length","bytes","lines","replacement_chars","complete")})
# 非法扩展名
r=requests.post(BASE+"/api/text/upload",files={"file":("x.pdf",b"%PDF-1.4","application/pdf")},timeout=30)
print("pdf",r.status_code,r.text[:120])
# 工程保存
r=requests.post(BASE+"/api/project/save",json={},timeout=30); print("save",r.status_code,r.text[:120])
