import re, pathlib
md=pathlib.Path("运行与检测报告.md").read_text(encoding="utf-8")
links=re.findall(r'!\[[^\]]*\]\(([^)]+)\)',md)
missing=[]
for l in links:
    p=l.strip().strip('<>')
    if p.startswith('http'): continue
    if not pathlib.Path(p).exists(): missing.append(p)
print("images",len(links),"missing",missing)
# 普通链接（非图片）里的本地路径
links2=re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)',md)
miss2=[]
for l in links2:
    p=l.strip().strip('<>')
    if p.startswith('http') or p.startswith('#'): continue
    if not pathlib.Path(p).exists(): miss2.append(p)
print("links",len(links2),"missing",miss2)
