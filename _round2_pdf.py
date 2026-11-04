import re, pathlib
b=pathlib.Path(r"outputs\孔乙己_一致性优化_交付版\07_阅读\漫画阅读.pdf").read_bytes()
print("bytes",len(b),"pages",len(re.findall(rb"/Type\s*/Page[^s]",b)))
for m in re.finditer(rb"/MediaBox\s*\[([^\]]+)\]",b): print(m.group(1).decode())
