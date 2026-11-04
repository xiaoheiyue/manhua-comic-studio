import json
p=json.load(open(r"outputs\孔乙己_一致性优化_交付版\08_回溯\质量报告.json",encoding="utf-8"))
print([(x["分镜"],x["自检分"],x["问题"]) for x in p])
