import re, pathlib
b=pathlib.Path(r"outputs\孔乙己_最后的酒_最终版\07_阅读\漫画阅读.pdf").read_bytes()
print("bytes",len(b),"header",b[:8])
print("page_markers",len(re.findall(rb"/Type\s*/Page[^s]",b)))
for m in re.finditer(rb"/MediaBox\s*\[([^\]]+)\]",b):
    print("mediabox",m.group(1).decode()[:80])
