from PIL import Image, ImageDraw, ImageFont
import requests, pathlib
p=requests.get("http://127.0.0.1:7870/api/project",timeout=20).json()
for who in ("掌柜","我"):
    shots=[x for x in p["shots"] if who in x["characters"]]
    W,H=420,560; cols=len(shots) or 1
    sheet=Image.new("RGB",(W*cols,H+40),(240,240,240)); d=ImageDraw.Draw(sheet)
    try: font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",22)
    except: font=ImageFont.load_default()
    for i,s in enumerate(shots):
        im=Image.open(s["image_path"]).convert("RGB"); im.thumbnail((W,H))
        sheet.paste(im,(i*W+(W-im.width)//2,40+(H-im.height)//2))
        d.text((i*W+8,8),f'#{s["index"]}',fill=(0,0,0),font=font)
    out=pathlib.Path(f"outputs/_consistency_{who}.png"); sheet.save(out); print(out)
