import json, pathlib
a=json.load(open("data/projects/558f643800.json",encoding="utf-8"))
b=json.load(open("outputs/孔乙己_一致性优化_第二轮/09_清单/工程数据.json",encoding="utf-8"))
print("current keys",list(a.keys())[:30])
print("export keys",list(b.keys())[:30])
print("current pages",[(p.get("index"),p.get("layout_id"),p.get("shot_indices")) for p in a.get("pages") or []])
print("export pages",[(p.get("index"),p.get("layout_id"),p.get("shot_indices")) for p in b.get("pages") or []])
print("export shots",len(b.get("shots") or []),"chars",len(b.get("characters") or []))
