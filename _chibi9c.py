import sys
sys.path.insert(0,'.')
from comicgen import studio
meta=studio.generate({'mode':'emoji','topic':'和参考图角色一样的表情包','style_id':'FE-034','color_ids':['C-26'],'overlay_text':False,'emoji_grid':3,'reference_image':'outputs/20262280210_conv.png'},progress=lambda m: print(m,flush=True))
print(meta['image'])
