import requests, json
BASE="http://127.0.0.1:7870"
def call(method,path,**kw):
    r=getattr(requests,method)(BASE+path,timeout=30,**kw)
    try: body=r.json()
    except: body=r.text[:100]
    print(path, r.status_code, str(body)[:180])
call("post","/api/analyze",json={"text":"太短"})
call("post","/api/analyze",json={"text":""})
call("post","/api/layout/plan",json={"mode":"xxx","use_llm":False})
call("post","/api/project/render-mode",json={"mode":"xxx"})
call("post","/api/style/set",json={"style_id":"ZZ-999"})
call("post","/api/project/load",json={"file":"不存在的工程.json"})
call("get","/api/bubble/layout",params={"shot_id":"nope"})
call("post","/api/bubble/preview",json={"id":"nope"})
call("post","/api/page/set-layout",json={"page_id":"nope","layout_id":"SB-001"})
call("post","/api/studio/generate",json={})
