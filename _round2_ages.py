import requests, json
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
ages={"孔乙己":"四十岁上下","我":"十二三岁","掌柜":"中年","聚集的几个人":"中年"}
chars=[]
for c in p["characters"]:
    chars.append({"id":c["id"],"name":c["name"],"gender":c["gender"],"age":ages.get(c["name"],c["age"]),
                  "identity":c["identity"],"personality":c["personality"],"role":c["role"],"is_extra":c["is_extra"]})
r=requests.post(BASE+"/api/analysis/save",json={"characters":chars},timeout=30); print("age save",r.status_code)
p=requests.get(BASE+"/api/project").json()
me=next(c for c in p["characters"] if c["name"]=="我")
design=me["design"].replace("约十六七岁，身高中等偏瘦","约十二三岁，身形瘦小")
kw=[("瘦小单薄少年" if k=="清瘦窄肩少年" else k) for k in me["keywords"]]
if "十二三岁" not in " ".join(kw): kw.append("十二三岁瘦小少年")
r=requests.post(BASE+"/api/character/update",json={"id":me["id"],"design":design,"keywords":kw},timeout=30); print("me update",r.status_code)
p=requests.get(BASE+"/api/project").json()
for c in p["characters"]:
    print(c["name"],c["age"],len(c.get("design") or ""),c.get("keywords"))
