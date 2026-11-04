import requests, json
from comicgen import prompts
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
for c in p["characters"]:
    print("====",c["name"])
    print("DESIGN:",c.get("design")[:500])
    print("KEYWORDS:",c.get("keywords"))
    # wrapper object? use dict-like? prompts expects attributes
    class C: pass
    o=C()
    for k,v in c.items(): setattr(o,k,v)
    print("LOCK:",prompts.character_feature_lock(o))
