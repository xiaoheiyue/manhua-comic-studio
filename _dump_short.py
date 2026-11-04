import json, requests, pathlib
p=requests.get("http://127.0.0.1:7870/api/project",timeout=15).json()
out={"shots":[{k:s.get(k) for k in ("index","shot_type","camera","composition","lighting","panel_scale","beat","pacing","panel_emphasis","characters","scene","description","action","expression","dialogues","caption","hook")} for s in p["shots"]],
     "pages":[{k:pg.get(k) for k in ("index","layout_id","layout_name","panels","shot_indices","rationale","status")} for pg in (p.get("pages") or [])]}
pathlib.Path("outputs/_e2e_short_storyboard.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
lines=[]
for s in p["shots"]:
    lines.append(f'#{s["index"]:02d} {s.get("shot_type")}/{s.get("panel_scale")} {s.get("beat")} chars={s.get("characters")}')
    lines.append("  "+str(s.get("description")))
    for d in (s.get("dialogues") or []): lines.append(f'  dlg {d.get("speaker")}[{d.get("slot")}]: {d.get("text")}')
pathlib.Path("outputs/_e2e_short_storyboard_readable.txt").write_text("\n".join(lines),encoding="utf-8")
print("ok",len(p["shots"]))
