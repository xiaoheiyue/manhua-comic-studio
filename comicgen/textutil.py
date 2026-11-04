# -*- coding: utf-8 -*-
"""文本工具：文件读取、编码自动识别、小说分段、提示词辅助。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import config
from .logger import get_logger

log = get_logger(__name__)


class UnsupportedFileError(ValueError):
    """上传的不是文本格式文件（需求 4.1：提示"仅支持文本格式文件"）。"""


# =============================================================================
# 编码识别与读取
# =============================================================================

def detect_and_decode(raw: bytes) -> tuple[str, str]:
    """
    自动识别编码并解码。

    返回 (文本, 使用的编码)。全部失败时用 utf-8 + 忽略错误兜底。
    """
    # 1) BOM 优先
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le", errors="replace"), "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16-be", errors="replace"), "utf-16-be"

    # 2) 候选编码逐个严格解码
    for enc in config.ENCODING_CANDIDATES:
        if enc in ("utf-16-le", "utf-16-be", "latin-1"):
            continue  # 这两个太宽松，放到最后
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue

    # 3) chardet 猜测
    try:
        import chardet  # type: ignore

        guess = chardet.detect(raw) or {}
        enc = guess.get("encoding")
        if enc:
            try:
                return raw.decode(enc), enc
            except (UnicodeDecodeError, LookupError):
                pass
    except Exception as exc:  # noqa: BLE001
        log.debug("chardet 不可用或识别失败：%s", exc)

    # 4) 兜底
    try:
        return raw.decode("utf-16"), "utf-16"
    except (UnicodeDecodeError, LookupError):
        pass
    return raw.decode("utf-8", errors="replace"), "utf-8(容错)"


def read_text_file(path: str | Path) -> tuple[str, str, str]:
    """
    读取文本文件。

    返回 (文本, 编码, 文件名)。非文本扩展名抛 UnsupportedFileError。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在：{p}")
    ext = p.suffix.lower()
    if ext and ext not in config.SUPPORTED_TEXT_EXTENSIONS:
        raise UnsupportedFileError(
            f"仅支持文本格式文件，当前文件类型为 {ext}"
        )

    raw = p.read_bytes()
    if b"\x00" in raw[:4096] and ext not in (".txt", ".md", ""):
        # 含大量 NUL 的通常不是纯文本
        raise UnsupportedFileError("仅支持文本格式文件")

    text, enc = detect_and_decode(raw)
    text = normalize_novel(text)
    log.info("读取文件 %s（编码 %s，字符数 %d）", p.name, enc, len(text))
    return text, enc, p.name


def normalize_novel(text: str) -> str:
    """统一换行、去掉零宽字符与多余空行。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# =============================================================================
# 小说分段（长文分批送入模型）
# =============================================================================

def split_into_chunks(text: str,
                      chunk_size: int | None = None,
                      overlap: int | None = None) -> list[str]:
    """
    按段落边界把小说切成若干块，块间保留少量重叠，避免情节被切断。
    """
    text = normalize_novel(text)
    if not text:
        return []
    size = chunk_size or config.CHUNK_SIZE
    ov = overlap if overlap is not None else config.CHUNK_OVERLAP
    if len(text) <= size:
        return [text]

    paragraphs = [p for p in text.split("\n\n")]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        # 超长段落（无空行的网文）先按句号切
        pieces = _split_long_paragraph(para, size)
        for piece in pieces:
            if len(buf) + len(piece) + 2 <= size:
                buf = f"{buf}\n\n{piece}" if buf else piece
            else:
                if buf:
                    chunks.append(buf)
                # 重叠：把上一块尾部接到新块开头
                tail = buf[-ov:] if buf and ov > 0 else ""
                buf = (tail + "\n\n" + piece) if tail else piece
    if buf:
        chunks.append(buf)
    return chunks


def _split_long_paragraph(para: str, size: int) -> list[str]:
    """把超过 size 的段落按句子边界切小。"""
    para = para.strip()
    if len(para) <= size:
        return [para] if para else []
    sentences = re.split(r"(?<=[。！？!?…])", para)
    out: list[str] = []
    buf = ""
    for s in sentences:
        if not s:
            continue
        if len(s) > size:
            # 单句仍然过长，硬切
            for i in range(0, len(s), size):
                out.append(s[i:i + size])
            buf = ""
            continue
        if len(buf) + len(s) <= size:
            buf += s
        else:
            if buf:
                out.append(buf)
            buf = s
    if buf:
        out.append(buf)
    return out


def chunk_with_offsets(text: str,
                       chunk_size: int | None = None,
                       overlap: int | None = None) -> list[tuple[int, str]]:
    """返回 (起始偏移, 块内容) 列表，便于把分镜映射回原文位置。"""
    text = normalize_novel(text)
    chunks = split_into_chunks(text, chunk_size, overlap)
    result: list[tuple[int, str]] = []
    cursor = 0
    for ch in chunks:
        # 用块的首 30 字符定位
        probe = ch.strip()[:30]
        pos = text.find(probe, max(0, cursor - 200)) if probe else cursor
        if pos < 0:
            pos = cursor
        result.append((pos, ch))
        cursor = pos + len(ch)
    return result


# =============================================================================
# 文本小工具
# =============================================================================

def truncate(text: str, limit: int, tail: str = "…") -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + tail


def clean_model_text(text: str) -> str:
    """清理模型输出里常见的 markdown 包裹与多余空白。"""
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def unescape_text(text: str) -> str:
    """
    还原模型输出里的 HTML 转义（`&#39;` → `'`、`&quot;` → `"` 等）。

    必须在 JSON 解析**之后**按字段调用：解析前还原会把 `&quot;` 变成裸引号，
    直接把 JSON 结构弄坏。
    """
    if not text or "&" not in text:
        return text or ""
    if not re.search(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);", text):
        return text
    import html
    return html.unescape(text)


def parse_json_block(text: str, expect: str = "object"):
    """
    从模型输出里稳健地抽取 JSON。

    先直接解析；失败则从第一个 { 或 [ 开始做括号配对扫描。
    expect: "object" 或 "array"。
    """
    t = clean_model_text(text)
    if not t:
        return None

    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    open_ch, close_ch = ("{", "}") if expect == "object" else ("[", "]")
    start = t.find(open_ch)
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    candidate = t[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = t.find(open_ch, start + 1)
    log.warning("模型输出中未找到合法 JSON，前 200 字符：%s", truncate(t, 200))
    return None


def split_dialogue_lines(text: str) -> list[str]:
    """把一段对话文本按换行/分号拆成多条。"""
    if not text:
        return []
    parts = re.split(r"[\n；;]+", text)
    return [p.strip() for p in parts if p.strip()]


def guess_speaker(line: str) -> tuple[str, str]:
    """从"林晚：你好"这类文本里拆出 (说话人, 内容)。"""
    m = re.match(r"^\s*([^：:]{1,12})\s*[：:]\s*(.+)$", line or "")
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", (line or "").strip()
