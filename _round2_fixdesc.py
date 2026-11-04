import requests, json
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
shots=[]
for s in p["shots"]:
    item={k:s.get(k) for k in ("id","shot_type","characters","scene","description","mood","source_text","camera","composition","lighting","transition","panel_scale","focus","action","expression","time_of_day","beat","pacing","panel_emphasis","caption","char_sides","silent","hook")}
    item["dialogues"]=s.get("dialogues") or []
    if s["index"]==5 and s["characters"]==["掌柜"]:
        item["action"]="独自取笑，嘴角带讥讽"; item["description"]="单人反应镜头：掌柜，独自取笑，嘴角带讥讽；场景：咸亨酒店柜台；画面里只有掌柜一个人。"
    if s["index"]==6 and s["characters"]==["聚集的几个人"]:
        item["action"]="独自哄笑，脸上带着客的讪笑"; item["description"]="单人反应镜头：聚集的几个人，独自哄笑，脸上带着客的讪笑；场景：咸亨酒店柜台；画面里只有这一群酒客，没有其他角色。"
    shots.append(item)
r=requests.post(BASE+"/api/storyboard/save",json={"shots":shots},timeout=60)
print("save",r.status_code,r.text[:120])
p=requests.get(BASE+"/api/project").json()
for s in p["shots"]:
    if s["index"] in (5,6): print(s["index"],s["description"],s["action"])
print("pages",len(p["pages"]))
