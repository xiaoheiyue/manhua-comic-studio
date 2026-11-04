import requests,time,json,uuid
from pathlib import Path
base='http://127.0.0.1:8188'
ref='outputs/20262280210_conv.png'
with open(ref,'rb') as f:
 r=requests.post(base+'/upload/image',files={'image':('ref.png',f,'image/png')},data={'overwrite':'true'},timeout=60)
 print('upload',r.status_code,flush=True); name=r.json()['name']
g={
 '1':{'class_type':'UNETLoader','inputs':{'unet_name':'qwen_image_2.1_turbo_int8_convrot.safetensors','weight_dtype':'default'}},
 '2':{'class_type':'CLIPLoader','inputs':{'clip_name':'qwen3vl_8b_w4a8.safetensors','type':'qwen_image','device':'default'}},
 '3':{'class_type':'VAELoader','inputs':{'vae_name':'qwen_image_2.1_vae_bf16.safetensors'}},
 '4':{'class_type':'LoadImage','inputs':{'image':name}},
 '5':{'class_type':'TextEncodeQwenImage21','inputs':{'clip':['2',0],'prompt':'把参考图角色转成 Q版 chibi 表情包角色，2.5头身，白色背景，粗描边，水彩马克笔质感，保留白发、紫黑长袍、银色腰带、额间红纹、精灵耳。','negative_prompt':'写实,3D,照片,文字,水印,多个人物,场景','resolution':512,'images':{'image_1':['4',0]},'vae':['3',0]}},
 '6':{'class_type':'KSampler','inputs':{'seed':888001,'steps':6,'cfg':1.0,'sampler_name':'euler','scheduler':'simple','denoise':1.0,'model':['1',0],'positive':['5',0],'negative':['5',1],'latent_image':['5',2]}},
 '7':{'class_type':'VAEDecode','inputs':{'samples':['6',0],'vae':['3',0]}},
 '8':{'class_type':'SaveImage','inputs':{'filename_prefix':'qwen_edit_test','images':['7',0]}},
}
r=requests.post(base+'/prompt',json={'prompt':g,'client_id':uuid.uuid4().hex},timeout=60)
print('submit',r.status_code,r.text[:500],flush=True)
if r.status_code==200:
 pid=r.json()['prompt_id']
 for i in range(60):
  time.sleep(3); h=requests.get(base+f'/history/{pid}',timeout=30).json()
  if h:
   print('hist',json.dumps(h,ensure_ascii=False)[:1000],flush=True); break
  print('wait',i,flush=True)
