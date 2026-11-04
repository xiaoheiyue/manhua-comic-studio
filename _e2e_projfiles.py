import json, pathlib
for f in sorted(pathlib.Path("data/projects").glob("*.json"), key=lambda p:p.stat().st_mtime, reverse=True)[:6]:
    try:
        d=json.loads(f.read_text(encoding="utf-8")); print(f.name,repr(d.get("title")),len(d.get("characters") or []),len(d.get("shots") or []),f.stat().st_mtime)
    except Exception as e: print(f.name,e)
