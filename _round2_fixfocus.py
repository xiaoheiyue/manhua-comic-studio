import requests
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json(); shots=[]
for s in p["shots"]:
    item={k:s.get(k) for k in ("id","shot_type","characters","scene","description","mood","source_text","camera","composition","lighting","transition","panel_scale","focus","action","expression","time_of_day","beat","pacing","panel_emphasis","caption","char_sides","silent","hook")}
    item["dialogues"]=s.get("dialogues") or []
    if s["index"]==5:
        item["expression"]="掌柜的表情：讥讽、得意；情绪集中在脸上"; item["focus"]="掌柜的脸与手部动作"
    if s["index"]==6:
        item["expression"]="聚集的几个人的表情：麻木、看热闹；情绪集中在脸上"; item["focus"]="人群的脸与动作"
    shots.append(item)
r=requests.post(BASE+"/api/storyboard/save",json={"shots":shots},timeout=60); print("save",r.status_code)
p=requests.get(BASE+"/api/project").json()
for s in p["shots"]:
    if s["index"] in (5,6): print(s["index"],s["expression"],"|",s["focus"])
