import json, pathlib
p=pathlib.Path("data/projects/558f643800_good.json")
d=json.loads(p.read_text(encoding="utf-8"))
for s in d["shots"]:
    if s["index"]==5:
        s["gaze"]="看向画面前下方"; s["focus"]="掌柜的脸与手部动作"
    if s["index"]==6:
        s["gaze"]="看向画面前下方"; s["focus"]="主要人物的脸与手部动作"
p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
print("patched")
