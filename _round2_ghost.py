import requests, json
p=requests.get("http://127.0.0.1:7870/api/project").json()
for s in p["shots"]:
    if s["index"] in (5,6):
        print("SHOT",s["index"],s["characters"])
        for k in ("description","action","expression","focus","gaze","scene","source_text"):
            print(" ",k,":",s.get(k))
        print(" dlg",[(d.get("speaker"),d.get("text")) for d in s.get("dialogues") or []])
