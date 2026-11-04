from PIL import Image, ImageDraw, ImageFont
import pathlib
try: font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",26)
except: font=ImageFont.load_default()
old=pathlib.Path("outputs/孔乙己_最后的酒_交付版/05_分镜图")
new=pathlib.Path("data/shots/558f643800")
items=[("孔乙己",old/"分镜_004.png",new/"分镜_004.png"),("掌柜",old/"分镜_003.png",new/"分镜_003.png"),("我",old/"分镜_001.png",new/"分镜_001.png")]
W,H=500,700; sheet=Image.new("RGB",(W*3,(H+50)*2+20),(245,245,245)); d=ImageDraw.Draw(sheet)
for row,label in enumerate(("优化前","优化后")):
    y0=10+row*(H+50)
    d.text((10,y0),label,fill=(180,30,30),font=font)
    for col,(who,po,pn) in enumerate(items):
        p=po if row==0 else pn
        im=Image.open(p).convert("RGB"); im.thumbnail((W-20,H-20))
        x=col*W+(W-im.width)//2; y=y0+45+(H-im.height)//2
        sheet.paste(im,(x,y))
        d.text((col*W+10,y0+10),who,fill=(0,0,0),font=font)
sheet.save("outputs/_round2_before_after.png"); print(sheet.size)
