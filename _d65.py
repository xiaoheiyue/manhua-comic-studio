import sys
sys.path.insert(0,'.')
from comicgen import studio
from comicgen.imageclient import ImageClient
pr,neg,meta=studio.build_prompt({'mode':'emoji','topic':'和参考图角色一样的表情包','style_id':'FE-034','color_ids':['C-26'],'overlay_text':False,'emoji_grid':3})
ref=studio._emoji_face_ref('outputs/创作工坊/refs/ref_c2cbc9b922.png')
exprs=studio._EMOJI_EXPRESSIONS[:9]; imgs=[]
for i,x in enumerate(exprs):
 prompt=f"同一角色，头肩/半身贴纸，白色纯背景，粗描边，贴纸感；表情：{x}；严格保持参考图的脸型、发型、服装与画风；表情清晰，不要文字、不要水印、不要分镜框。"
 c=ImageClient(workflow='image_z_image_turbo_int8_studio_emoji65')
 r=c.generate(prompt,reference_paths=[ref],negative_prompt=neg,size=(512,512),seed=777001)
 imgs.append(r.image)
studio._compose_emoji_grid(imgs,3).save('outputs/测试_表情包9_d65_sameseed.png'); print('ok')
