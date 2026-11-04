import requests
from comicgen import prompts
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
for c in p["characters"]:
    print(c["name"], "hard_features=", c.get("hard_features"))
    class C: pass
    o=C()
    for k,v in c.items(): setattr(o,k,v)
    print(" hair=",prompts._character_hair(o))
    print(" eyes=",prompts._character_eyes(o))
    print(" outfit=",prompts._character_outfit(o)[:120])
    print(" colors=",prompts._character_color_words(o))
