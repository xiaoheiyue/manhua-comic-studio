from PIL import Image
import pathlib
src=pathlib.Path("outputs/创作工坊/20261031_153518/emoji_FA-005_888001.png")
im=Image.open(src).convert("RGB")
w,h=im.size
# 裁左上角一格
cell=im.crop((0,0,w//3,h//3)).resize((512,512))
out=pathlib.Path("outputs/_studio_ref_cat.png"); cell.save(out)
print(out,cell.size)
