import json, requests, pathlib, time
BASE="http://127.0.0.1:7870"
text=pathlib.Path("outputs/_e2e_孔乙己_节选.txt").read_text(encoding="utf-8")
r=requests.post(BASE+"/api/project/new",json={"title":"孔乙己·最后的酒（真机全流程）","text":text},timeout=30)
p=r.json(); print("project",p["id"],len(p["novel_text"]))
r=requests.post(BASE+"/api/analyze",json={"text":text,"title":p["title"]},timeout=30)
job=r.json()["job_id"]; t0=time.time()
while time.time()-t0<300:
    time.sleep(2); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
    if j.get("state")!="running": print("analyze",j.get("state"),j.get("error"),round(time.time()-t0,1)); break
p=requests.get(BASE+"/api/project").json()
print("chars",[(c["name"],c["role"],c["is_extra"],c["id"]) for c in p["characters"]])
# 只保留孔乙己/掌柜做主参考，其余标为群众角色（仍然参与分镜提示词）
chars=[]
for c in p["characters"]:
    chars.append({"id":c["id"],"name":c["name"],"gender":c["gender"],"age":c["age"],
                  "identity":c["identity"],"personality":c["personality"],"role":c["role"],
                  "is_extra": c["name"] not in ("孔乙己","掌柜")})
r=requests.post(BASE+"/api/analysis/save",json={"characters":chars},timeout=30)
print("save",r.status_code)
p=requests.get(BASE+"/api/project").json()
print("after",[(c["name"],c["is_extra"]) for c in p["characters"]])
pathlib.Path("outputs/_e2e_short_analysis.json").write_text(json.dumps({"id":p["id"],"title":p["title"],"summary":p["summary"],"style":p["style"],"style_id":p.get("style_id"),"characters":[{"name":c["name"],"role":c["role"],"is_extra":c["is_extra"]} for c in p["characters"]]},ensure_ascii=False,indent=2),encoding="utf-8")
