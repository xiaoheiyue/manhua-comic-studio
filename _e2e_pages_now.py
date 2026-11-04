import requests, json
BASE="http://127.0.0.1:7870"
p=requests.get(BASE+"/api/project").json()
for pg in p["pages"]:
    print(pg["index"],pg["id"],pg["layout_id"],pg["panels"],pg["shot_indices"],pg["status"],pg.get("image_path"))
print("render",p.get("render_mode"))
