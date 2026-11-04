import json, pathlib, requests, re, time
from comicgen.qwenedit import QwenEditClient
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
s=next(x for x in p["shots"] if x["index"]==2)
c=next(x for x in p["characters"] if x["name"]=="孔乙己")
md=pathlib.Path("outputs/孔乙己_最后的酒_交付版/04_提示词/运行期/06_image/002_shot_002.md").read_text(encoding="utf-8")
prompt=md.split("```text",2)[2].split("```",1)[0].strip()
print("prompt chars",len(prompt),"main",c["main_visual"],"pose",s.get("pose_skeleton"))
t0=time.time()
img=QwenEditClient().generate_control(prompt, c["main_visual"], s["pose_skeleton"], size=(736,1280), seed=int(c.get("seed") or 888001), steps=8, strength=0.8, timeout=600)
img.save("outputs/_test_edit_pose_shot2.png")
print("done",img.size,round(time.time()-t0,1))
