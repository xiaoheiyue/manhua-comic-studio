import requests, json, time
BASE="http://127.0.0.1:7870"
for fam in ("zimage","qwen"):
    r=requests.post(BASE+"/api/model/family",json={"family":fam},timeout=30); print(fam,r.status_code,r.json().get("message"))
    time.sleep(1)
    s=requests.get(BASE+"/api/service/status",timeout=30).json()
    print("status family",s.get("image_family"),"image_ok",s.get("image",{}).get("ok"),str(s.get("image",{}).get("message"))[:100])
    c=requests.get(BASE+"/api/config",timeout=30).json(); print("config family",c.get("image_family"),"model",c.get("image_model"))
