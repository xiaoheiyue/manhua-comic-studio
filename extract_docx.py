# -*- coding: utf-8 -*-
"""提取 docx 小说文本，并定位章节结构。"""
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, '.')

DOCX = Path(sys.argv[1] if len(sys.argv) > 1 else '痕迹_卷一_入局.docx')


def read_docx(path: Path) -> str:
    """不依赖第三方库读取 docx 正文（docx 本质是 zip + XML）。"""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.startswith('word/') and n.endswith('.xml')]
        xml = z.read('word/document.xml').decode('utf-8', errors='replace')
    # 段落 -> 换行
    xml = re.sub(r'</w:p\s*>', '\n', xml)
    # 制表符与换行
    xml = xml.replace('<w:tab/>', '\t').replace('<w:br/>', '\n')
    # 去掉所有标签
    text = re.sub(r'<[^>]+>', '', xml)
    # 反转义
    for a, b in (('&lt;', '<'), ('&gt;', '>'), ('&amp;', '&'),
                 ('&quot;', '"'), ('&apos;', "'")):
        text = text.replace(a, b)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


text = read_docx(DOCX)
print('文件：', DOCX.name)
print('总字符数：', len(text))

# 找出章节标题
patterns = [
    r'^\s*第[一二三四五六七八九十百零〇\d]+章.*$',
    r'^\s*第[一二三四五六七八九十百零〇\d]+节.*$',
    r'^\s*Chapter\s*\d+.*$',
]
heads = []
for i, line in enumerate(text.split('\n')):
    s = line.strip()
    if not s or len(s) > 40:
        continue
    for p in patterns:
        if re.match(p, s):
            heads.append((i, s))
            break

print('\n=== 识别到的章节标题 ===')
for idx, h in heads[:40]:
    print(f'  行{idx:5d}  {h}')
if not heads:
    print('  （未识别到章节标题，将按长度切分）')

print('\n=== 开头 40 行 ===')
for i, line in enumerate(text.split('\n')[:40]):
    if line.strip():
        print(f'{i:4d}| {line[:80]}')
