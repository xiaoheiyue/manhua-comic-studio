# -*- coding: utf-8 -*-
"""项目级提示词覆盖。

用户可以在界面里查看当前工程的默认提示词，并保存自己的版本。保存后的文本
存在 ``Project.prompt_overrides`` 里，下一次真正调用对应 prompt builder 时，
由这里把 system / user（或图像提示词）替换掉。

键的格式：``<prompt_id>::<item>``，例如：

    analysis.overview::chunk:1
    character.design::character:ab12cd34
    image.shot_prompt::shot:ef567890
    narration.fill::global

这样同一条提示词可以按文段 / 角色 / 分镜分别覆盖，不会互相串用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def make_key(prompt_id: str, item: str = "global") -> str:
    return f"{prompt_id}::{item or 'global'}"


def get(project: Any, prompt_id: str, item: str = "global") -> dict[str, str] | None:
    """读取一条覆盖；没有返回 None。"""
    data = getattr(project, "prompt_overrides", None)
    if not isinstance(data, dict):
        return None
    value = data.get(make_key(prompt_id, item))
    return dict(value) if isinstance(value, dict) else None


def set_override(project: Any, prompt_id: str, item: str = "global", *,
                 system: str | None = None, user: str | None = None,
                 text: str | None = None) -> dict[str, str]:
    """保存覆盖；传入空字符串表示该字段留空（不会删除整条覆盖）。"""
    data = getattr(project, "prompt_overrides", None)
    if not isinstance(data, dict):
        data = {}
        project.prompt_overrides = data
    payload: dict[str, str] = {}
    if system is not None:
        payload["system"] = str(system)
    if user is not None:
        payload["user"] = str(user)
    if text is not None:
        payload["text"] = str(text)
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data[make_key(prompt_id, item)] = payload
    if hasattr(project, "touch"):
        project.touch()
    return payload


def clear(project: Any, prompt_id: str, item: str = "global") -> bool:
    data = getattr(project, "prompt_overrides", None)
    if not isinstance(data, dict):
        return False
    removed = data.pop(make_key(prompt_id, item), None)
    if removed is not None and hasattr(project, "touch"):
        project.touch()
    return removed is not None


def count(project: Any, prompt_id: str) -> int:
    data = getattr(project, "prompt_overrides", None)
    if not isinstance(data, dict):
        return 0
    prefix = f"{prompt_id}::"
    return sum(1 for k in data if k.startswith(prefix))


def apply_messages(project: Any, prompt_id: str, item: str,
                   messages: list[dict]) -> list[dict]:
    """把保存的 system / user 覆盖应用到 messages 上。"""
    override = get(project, prompt_id, item)
    if not override:
        return messages
    out: list[dict] = []
    for msg in messages:
        role = str(msg.get("role", ""))
        if role in ("system", "user") and role in override:
            out.append({**msg, "content": str(override.get(role) or "")})
        else:
            out.append(msg)
    return out


def text(project: Any, prompt_id: str, item: str, default: str) -> str:
    """读取纯文本提示词覆盖（图像提示词 / 气泡指令）。"""
    override = get(project, prompt_id, item)
    if override:
        value = override.get("text")
        if value is not None and str(value).strip():
            return str(value)
    return default
