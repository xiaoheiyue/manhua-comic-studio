import requests
from comicgen import prompts
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
class C: pass
for c in p["characters"]:
    o=C()
    for k,v in c.items(): setattr(o,k,v)
    lock=prompts.character_feature_lock(o)
    print(c["name"],len(lock)); print(lock)
