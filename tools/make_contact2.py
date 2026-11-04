from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(r"E:\manhua")
OUT = ROOT / "角色分镜一致性资料" / "assets"
font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 22)
small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 16)


def card(path, label, w=260, h=360):
    im = Image.open(path).convert("RGB")
    im.thumbnail((w - 10, h - 64), Image.LANCZOS)
    c = Image.new("RGB", (w, h), "white")
    c.paste(im, ((w - im.width) // 2, 4))
    d = ImageDraw.Draw(c)
    d.rectangle([0, h - 58, w, h], fill=(242, 242, 242))
    lines = []
    cur = ""
    for ch in label:
        if len(cur) >= 12:
            lines.append(cur)
            cur = ""
        cur += ch
    if cur:
        lines.append(cur)
    y = h - 52
    for line in lines[:2]:
        d.text((6, y), line, fill=(20, 20, 20), font=small)
        y += 21
    d.rectangle([0, 0, w - 1, h - 1], outline=(210, 210, 210), width=2)
    return c


items = [
    ("001", "特写｜孔乙己"),
    ("002", "近景｜掌柜"),
    ("003", "中景｜我（重跑后单人）"),
    ("004", "远景｜孔乙己 0参考"),
    ("005", "远景背影｜0参考（单人）"),
    ("006", "侧面｜我（side）"),
    ("007", "双人｜掌柜+短衣主顾"),
    ("008", "双人｜我+掌柜（关系句）"),
    ("009", "三人｜孔乙己+我+掌柜"),
    ("010", "四人｜多画了一个少年"),
]
cards = [card(ROOT / "data" / "shots" / "ctestg02" / f"分镜_{i}_raw.png", label)
         for i, label in items]
cols = 5
rows = 2
w = cols * (260 + 18) + 18
h = rows * (360 + 18) + 50
canvas = Image.new("RGB", (w, h), (250, 250, 250))
ImageDraw.Draw(canvas).text((18, 10), "第二轮 10 格：单人/双人/三人/四人（孔乙己工程）",
                            fill=(0, 0, 0), font=font)
for i, c in enumerate(cards):
    r, col = divmod(i, cols)
    canvas.paste(c, (18 + col * (260 + 18), 42 + r * (360 + 18)))
canvas.save(OUT / "07_第二轮一致性测试.png", format="PNG")
print("ok", canvas.size)
