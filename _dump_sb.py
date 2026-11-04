import json
p=json.load(open("outputs/_e2e_storyboard_real.json",encoding="utf-8"))
lines=[]
for s in p["shots"]:
    lines.append(f'#{s["index"]:02d} {s.get("shot_type")}/{s.get("panel_scale")} beat={s.get("beat")} chars={s.get("characters")} scene={s.get("scene")}')
    lines.append(f'   desc: {s.get("description")}')
    lines.append(f'   action: {s.get("action")} | expr: {s.get("expression")} | cam: {s.get("camera")} | light: {s.get("lighting")}')
    ds=s.get("dialogues") or []
    if ds:
        lines.append("   dlg: "+" / ".join(f'{d.get("speaker")}[{d.get("slot")}]:{d.get("text")}' for d in ds))
    if s.get("caption"): lines.append(f'   caption: {s["caption"]}')
open("outputs/_e2e_storyboard_readable.txt","w",encoding="utf-8").write("\n".join(lines))
print("ok",len(lines))
