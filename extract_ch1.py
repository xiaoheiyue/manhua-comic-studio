# -*- coding: utf-8 -*-
"""定位第一章边界并导出为纯文本。"""
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, '.')
from extract_docx import read_docx  # noqa: E402

DOCX = Path('痕迹_卷一_入局.docx')
text = read_docx(DOCX)
lines = text.split('\n')

# 章节标题形如 "第 1 章 · 委托"
head_re = re.compile(r'^\s*第\s*(\d+)\s*章\s*[·\-—]?\s*(.*)$')
heads = []
for i, line in enumerate(lines):
    m = head_re.match(line.strip())
    if m:
        heads.append((i, int(m.group(1)), m.group(2).strip()))

print('=== 章标题位置 ===')
for i, n, t in heads:
    print(f'  行{i:5d}  第 {n} 章 · {t}')

# 目录区在开头（每章标题相隔 2 行），正文从相隔较远的那一批开始
body_heads = []
for k, (i, n, t) in enumerate(heads):
    nxt = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
    if nxt - i > 10:          # 正文段落间距远大于目录
        body_heads.append((i, n, t))

print('\n=== 正文章节位置 ===')
for i, n, t in body_heads:
    print(f'  行{i:5d}  第 {n} 章 · {t}')

ch1_head = next((h for h in body_heads if h[1] == 1), body_heads[0] if body_heads else None)
if ch1_head is None:
    raise SystemExit('未定位到正文第一章')
start = ch1_head[0]
after = [h for h in body_heads if h[0] > start]
end = after[0][0] if after else len(lines)

ch1_lines = lines[start:end]
ch1 = '\n'.join(ch1_lines).strip()
ch1 = re.sub(r'\n{3,}', '\n\n', ch1)

print(f'\n=== 第一章 ===')
print(f'起始行 {start}，结束行 {end}，共 {len(ch1_lines)} 行，{len(ch1)} 字')
print('--- 前 600 字 ---')
print(ch1[:600])
print('...')
print('--- 后 300 字 ---')
print(ch1[-300:])

out = Path('first_chapter.txt')
out.write_text(ch1, encoding='utf-8')
print(f'\n已写出：{out.resolve()}')

# 统计对话数量（粗略）
quotes = re.findall(r'[“"]([^”"]{1,200})[”"]', ch1)
print('引号对话条数：', len(quotes))
