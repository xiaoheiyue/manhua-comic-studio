import requests
from comicgen import castguard
from comicgen.models import Project
p=requests.get("http://127.0.0.1:7870/api/project").json()
# 用工程文件加载，避免序列化差异
proj=Project.load("data/projects/558f643800_good.json")
for s in proj.shots:
    if s.index in (5,6):
        print("SHOT",s.index,s.characters)
        for attr in ("description","action","expression","focus","gaze","scene","source_text"):
            print(" ",attr,repr(getattr(s,attr,None)))
        print(castguard.diagnose(proj,s))
