import json
d=json.load(open("outputs/_studio_round2_results.json",encoding="utf-8"))
for x in d:
    m=x.get("meta") or {}
    print(x["mode"],"ok",x.get("ok"),"sec",x.get("seconds"),"image",m.get("image"))
    print("   keys",list(m.keys()),"stickers",len(m.get("stickers") or []),"ref",m.get("reference_mode"))
