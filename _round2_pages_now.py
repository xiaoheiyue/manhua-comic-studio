import requests
p=requests.get("http://127.0.0.1:7870/api/project").json()
for pg in p["pages"]: print(pg["index"],pg["layout_id"],pg["panels"],pg["shot_indices"],pg["status"])
print("shots",len(p["shots"]))
