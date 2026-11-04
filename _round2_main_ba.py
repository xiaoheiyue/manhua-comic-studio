from PIL import Image, ImageDraw, ImageFont
import pathlib
try: font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",26); big=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",30)
except: font=big=ImageFont.load_default()
old=pathlib.Path("outputs/孔乙己_最后的酒_交付版/02_角色")
new=pathlib.Path("data/characters")
items=[("孔乙己",old/"孔乙己/主视图.png",new/"孔乙己/默认造型/主视图.png"),("掌柜",old/"掌柜/主视图.png",new/"掌柜/默认造型/主视图.png")]
W,H=460,700; sheet=Image.new("RGB",(W*2,(H+80)*2+10),(245,245,245)); d=ImageDraw.Draw(sheet)
for row,label in enumerate(("优化前主视图","优化后主视图")):
    y0=5+row*(H+80); d.text((10,y0),label,fill=(180,30,30),font=big)
    for col,(who,po,pn) in enumerate(items):
        d.text((col*W+10,y0+38),who,fill=(0,0,0),font=font)
        p=po if row==0 else pn
        im=Image.open(p).convert("RGB"); im.thumbnail((W-20,H-20))
        sheet.paste(im,(col*W+(W-im.width)//2,y0+70+(H-im.height)//2))
sheet.save("outputs/_round2_main_before_after.png"); print(sheet.size)
