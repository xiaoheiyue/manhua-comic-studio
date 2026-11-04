import json, requests, pathlib
BASE="http://127.0.0.1:7870"
out={}
def get(path):
    try:
        r=requests.get(BASE+path,timeout=30); return r.status_code, (r.json() if 'json' in r.headers.get('content-type','') else r.text[:200])
    except Exception as e:
        return "ERR", str(e)
for p in ["/api/sample","/api/rules","/api/export/list","/api/project/list","/api/studio/catalog","/api/studio/history","/api/studio/stickers","/api/logs"]:
    code,data=get(p)
    if isinstance(data,dict):
        out[p]={"code":code,"keys":list(data.keys())}
        if p=="/api/sample": out[p]["length"]=len(data.get("text","")); out[p]["title"]=data.get("title")
        if p=="/api/rules": out[p]["summary"]={k:(len(v) if isinstance(v,(list,dict,str)) else v) for k,v in data.items()}
        if p=="/api/export/list": out[p]["items"]=data.get("items",[])[:8]
        if p=="/api/project/list": out[p]["count"]=len(data.get("projects",[])); out[p]["first"]=data.get("projects",[])[:2]
        if p=="/api/studio/catalog": out[p].update({k:(len(v) if isinstance(v,list) else v) for k,v in data.items() if not isinstance(v,(dict,))})
        if p=="/api/logs": out[p]["lines"]=len(data.get("lines",[]))
    else:
        out[p]={"code":code,"data":str(data)[:200]}
# 风格与版式列表单独统计
for p,key in [("/api/style/list","styles"),("/api/layout/list","layouts")]:
    code,data=get(p); out[p]={"code":code}
    if isinstance(data,dict):
        out[p]["keys"]=list(data.keys())
        for k,v in data.items():
            if isinstance(v,list): out[p][k]=len(v)
code,data=get("/api/style/list")
if isinstance(data,dict):
    st=data.get("styles") or []
    out["/api/style/list"]["sample"]=[{k:s.get(k) for k in ("id","name","group","trigger")} for s in st[:3]]
    out["/api/style/list"]["colors"]=len(data.get("colors") or [])
    out["/api/style/list"]["tones"]=len(data.get("tones") or [])
code,data=get("/api/layout/list")
if isinstance(data,dict):
    lay=data.get("layouts") or []
    out["/api/layout/list"]["sample"]=[{k:s.get(k) for k in ("id","name","panels","geometry","mode")} for s in lay[:5]]
pathlib.Path("outputs/_e2e_readonly.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print("saved")
