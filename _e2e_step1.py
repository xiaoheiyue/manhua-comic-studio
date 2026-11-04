import json, requests, pathlib, time
BASE = "http://127.0.0.1:7870"
text = pathlib.Path("data/uploads/孔乙己_网络文本.txt").read_text(encoding="utf-8")
print("chars", len(text))
r = requests.post(BASE+"/api/project/new", json={"title":"孔乙己·咸亨酒店（检测）","text":text}, timeout=30)
print("new", r.status_code)
p = r.json(); print("id", p["id"], "title", p["title"], "text", len(p["novel_text"]))
# 保存并开始分析
r = requests.post(BASE+"/api/analyze", json={"text":text,"title":"孔乙己·咸亨酒店（检测）"}, timeout=30)
print("analyze", r.status_code, r.text[:200])
job = r.json().get("job_id")
for i in range(120):
    time.sleep(1)
    j = requests.get(f"{BASE}/api/job/{job}", timeout=10).json()
    if j.get("state") != "running":
        print("job", j.get("state"), j.get("message"), "err", j.get("error"))
        break
else:
    print("timeout")
print("project chars:", len(requests.get(BASE+"/api/project").json()["characters"]))
