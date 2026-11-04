from PIL import Image, ImageDraw, ImageFont
import requests, json, pathlib
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
out=pathlib.Path("outputs/_consistency_before.png")
W,H=480,640; cols=4; rows=1
sheet=Image.new("RGB",(W*cols,H*rows+40),(240,240,240)); d=ImageDraw.Draw(sheet)
try: font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",24)
except: font=ImageFont.load_default()
for i,s in enumerate([x for x in p["shots"] if "孔乙己" in x["characters"]]):
    im=Image.open(s["image_path"]).convert("RGB")
    im.thumbnail((W,H))
    x=i*W+(W-im.width)//2; y=40+(H-im.height)//2
    sheet.paste(im,(x,y))
    d.text((i*W+8,8),f'#{s["index"]} {"、".join(s["characters"])}',fill=(0,0,0),font=font)
sheet.save(out)
print("saved",out,sheet.size)
