import requests, json
from comicgen import prompts
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
for c in p["characters"]:
    print("====",c["name"])
    print("hard",json.dumps(c.get("hard_features"),ensure_ascii=False))
    class C: pass
    o=C()
    for k,v in c.items(): setattr(o,k,v)
    print("lock",prompts.character_feature_lock(o))
    print("design",c.get("design","")[:300])
