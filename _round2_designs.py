import requests, time, json, pathlib
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
for c in p["characters"]:
    r=requests.post(BASE+"/api/character/design",json={"id":c["id"]},timeout=30)
    print("design",c["name"],r.status_code)
    job=r.json().get("job_id")
    t0=time.time()
    while job and time.time()-t0<180:
        time.sleep(1); j=requests.get(f"{BASE}/api/job/{job}",timeout=10).json()
        if j.get("state")!="running":
            print("  ->",j.get("state"),j.get("error")); break
p=requests.get(BASE+"/api/project").json()
for c in p["characters"]:
    print("====",c["name"],c["age"],len(c.get("design") or "")); print(c.get("design","")[:220]); print("KW",c.get("keywords"))
pathlib.Path("outputs/_round2_designs.json").write_text(json.dumps([{k:c.get(k) for k in ("id","name","age","design","keywords","hard_features")} for c in p["characters"]],ensure_ascii=False,indent=2),encoding="utf-8")
