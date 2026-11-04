import re, pathlib
for fn in ("角色一致性优化与功能验证报告.md","非漫画创作功能检测报告.md"):
    md=pathlib.Path(fn).read_text(encoding="utf-8")
    imgs=re.findall(r'!\[[^\]]*\]\(([^)]+)\)',md)
    miss=[]
    for l in imgs:
        p=l.strip().strip('<>')
        if p.startswith('http'): continue
        if not pathlib.Path(p).exists(): miss.append(p)
    print(fn,"images",len(imgs),"missing",miss)
