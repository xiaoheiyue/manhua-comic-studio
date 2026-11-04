import requests, pathlib, json
BASE="http://127.0.0.1:7870"
text=pathlib.Path("outputs/_e2e_孔乙己_节选.txt").read_text(encoding="utf-8")
r=requests.post(BASE+"/api/project/new",json={"title":"孔乙己·一致性优化（第二轮）","text":text},timeout=30)
p=r.json(); print("id",p["id"],"chars",len(p["novel_text"]))
pathlib.Path("outputs/_round2_project.json").write_text(json.dumps({"id":p["id"],"title":p["title"],"chars":len(p["novel_text"])},ensure_ascii=False,indent=2),encoding="utf-8")
