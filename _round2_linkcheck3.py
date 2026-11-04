import re, pathlib
for fn in ("角色一致性优化与功能验证报告.md","非漫画创作功能检测报告.md"):
    md=pathlib.Path(fn).read_text(encoding="utf-8")
    imgs=re.findall(r'!\[[^\]]*\]\(([^)]+)\)',md)
    miss=[l for l in imgs if not l.strip().strip('<>').startswith('http') and not pathlib.Path(l.strip().strip('<>')).exists()]
    print(fn,"images",len(imgs),"missing",miss,"lines",len(md.splitlines()))
