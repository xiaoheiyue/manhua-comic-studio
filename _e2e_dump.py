import json, requests
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
out={
 "id":p["id"],"title":p["title"],"summary":p["summary"],"plotline":p["plotline"],
 "style":p["style"],"style_detail":p["style_detail"],
 "characters":[{"name":c["name"],"gender":c["gender"],"identity":c["identity"],"role":c["role"]} for c in p["characters"]],
 "scenes":[{"name":s["name"],"features":s["features"]} for s in p["scenes"]],
 "style_id":p.get("style_id"),"style_colors":p.get("style_colors"),"tone":p.get("tone"),
}
open("outputs/_e2e_analysis.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))
print("saved")
