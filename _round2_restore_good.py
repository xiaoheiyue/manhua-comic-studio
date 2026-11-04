import json, pathlib, shutil
src=pathlib.Path("outputs/孔乙己_一致性优化_第二轮/09_清单/工程数据.json")
d=json.loads(src.read_text(encoding="utf-8"))
for s in d.get("shots") or []:
    if s.get("index")==5:
        s["expression"]="掌柜的表情：讥讽、得意；情绪集中在脸上"
        s["focus"]="掌柜的脸与手部动作"
    if s.get("index")==6:
        s["expression"]="聚集的几个人的表情：麻木、看热闹；情绪集中在脸上"
        s["focus"]="人群的脸与动作"
out=pathlib.Path("data/projects/558f643800_good.json")
out.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
print("pages",[(p.get("index"),p.get("layout_id"),p.get("shot_indices")) for p in d.get("pages") or []])
