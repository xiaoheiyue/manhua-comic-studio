import pathlib
raw=pathlib.Path("data/uploads/孔乙己_正文.txt").read_text(encoding="utf-8")
start=raw.find("中秋过后")
seg=raw[start:].strip()
out=pathlib.Path("outputs/_e2e_孔乙己_节选.txt"); out.write_text(seg,encoding="utf-8")
print("chars",len(seg),"paras",len([x for x in seg.splitlines() if x.strip()]))
