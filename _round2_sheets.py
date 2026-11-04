from PIL import Image, ImageDraw, ImageFont
import requests, pathlib
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
try: font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",24)
except: font=ImageFont.load_default()
def sheet(who,out):
    shots=[s for s in p["shots"] if who in s["characters"]]
    W,H=420,560; cols=max(1,len(shots))
    im=Image.new("RGB",(W*cols,H+40),(240,240,240)); d=ImageDraw.Draw(im)
    for i,s in enumerate(shots):
        x=Image.open(s["image_path"]).convert("RGB"); x.thumbnail((W,H))
        im.paste(x,(i*W+(W-x.width)//2,40+(H-x.height)//2))
        d.text((i*W+8,8),f'#{s["index"]} {who}',fill=(0,0,0),font=font)
    im.save(out); print(out,im.size)
for who in ("孔乙己","掌柜","我","聚集的几个人"):
    sheet(who,f"outputs/_round2_{who}.png")
