# -*- coding: utf-8 -*-
"""文本大模型客户端（DeepSeek-V4.1-Flash，OpenAI 兼容协议）。

负责：小说分析、人设生成、分镜拆解等所有"用文字理解文字"的任务。
只依赖 requests，不强制 openai SDK。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Sequence

import requests

import config
from .logger import get_logger
from .textutil import clean_model_text, parse_json_block, truncate

log = get_logger(__name__)

# 负缓存：host -> 不可用原因。
# Unsloth 原生状态端点 /api/inference/status 在 vLLM 等服务上并不存在；
# 一旦确认 404/被禁用，本进程内就不再重复探测，避免状态页每轮检查都打出一条 404。
_UNSLOTH_STATUS_UNAVAILABLE: dict[str, str] = {}


class LLMError(RuntimeError):
    """文本模型调用失败。"""


class AuthError(LLMError):
    """密钥缺失或无效（HTTP 401/403）。"""


class TextClient:
    """
    OpenAI 兼容的 chat/completions 客户端。

    默认指向 Unsloth 服务端的 /v1/chat/completions；若该路径不可用（404），
    自动回退到原生路径 /api/inference/chat/completions。
    """

    def __init__(self,
                 base_url: str | None = None,
                 model: str | None = None,
                 api_key: str | None = None,
                 timeout: int | None = None,
                 max_retries: int | None = None):
        self.base_url = (base_url or config.TEXT_API_BASE).rstrip("/")
        self.model = model or config.TEXT_MODEL
        self._explicit_key = api_key
        self.timeout = timeout or config.TEXT_TIMEOUT
        self.max_retries = max_retries if max_retries is not None else config.TEXT_MAX_RETRIES
        self.last_usage: dict[str, Any] = {}
        self._good_path: str = ""      # 记住可用的 chat 路径

    # ------------------------------------------------------------------
    @property
    def key(self) -> str:
        if self._explicit_key is not None:
            return self._explicit_key
        return config.text_api_key()

    @property
    def host(self) -> str:
        """服务根地址（去掉 /v1 后缀）。"""
        return self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url

    @property
    def is_local(self) -> bool:
        """是否为本地部署的服务（本地才有"加载模型/查加载状态"这回事）。"""
        return any(h in self.host for h in
                   ("127.0.0.1", "localhost", "0.0.0.0", "192.168.", "10.", "172."))

    @property
    def chat_paths(self) -> list[str]:
        """候选 chat 路径，按优先级排列。"""
        order = [f"{self.base_url}/chat/completions"]
        if self._good_path:
            order = [self._good_path] + [p for p in order if p != self._good_path]
        # 只有本地服务才尝试 Unsloth 原生路径
        if self.is_local:
            native = f"{self.host}/api/inference/chat/completions"
            if native not in order:
                order.append(native)
        return order

    @property
    def model_candidates(self) -> list[str]:
        """模型名候选：主模型优先，失败时依次尝试备选。"""
        out = [self.model]
        for name in getattr(config, "TEXT_MODEL_FALLBACKS", ()) or ():
            if name not in out:
                out.append(name)
        return out

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if not config.TEXT_API_NEEDS_KEY:
            return h
        key = self.key
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    # ------------------------------------------------------------------
    def chat(self,
             messages: Sequence[dict[str, str]],
             temperature: float | None = None,
             max_tokens: int | None = None,
             json_mode: bool = False,
             on_delta: Callable[[str], None] | None = None) -> str:
        """
        调用对话接口，返回助手文本。

        json_mode=True 时要求模型输出 JSON（部分服务支持 response_format）。
        on_delta 不为空时走流式，用于界面实时显示模型输出进度。
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": config.TEXT_TEMPERATURE if temperature is None else temperature,
            "top_p": config.TEXT_TOP_P,
            "max_tokens": max_tokens or config.TEXT_MAX_TOKENS,
            "stream": bool(on_delta),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if bool(getattr(config, "TEXT_DISABLE_THINKING", False)):
            # DeepSeek-V4.1-Flash：显式关闭思考链。分镜/人设是结构化任务，
            # 开着 reasoning 会把 max_tokens 吃光，返回 content 为空。
            payload["thinking"] = {"type": "disabled"}
        if on_delta and config.TEXT_SEND_STREAM_OPTIONS:
            payload["stream_options"] = {"include_usage": True}

        if config.TEXT_API_NEEDS_KEY and not self.key:
            raise LLMError(
                "未读取到 DeepSeek 密钥：请把 .env.example 复制为 .env 并填写 "
                "DEEPSEEK_API_KEY，或设置同名系统环境变量后重启程序。\n"
                "  申请地址：https://platform.deepseek.com/api_keys")

        last_err: Exception | None = None
        model_try = list(self.model_candidates)
        for attempt in range(1, self.max_retries + 1):
            payload["model"] = model_try[min(attempt - 1, len(model_try) - 1)]
            try:
                if on_delta:
                    return self._stream(payload, on_delta)

                text = self._request_chat(payload, json_mode)
                return text

            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                wait = config.TEXT_RETRY_BACKOFF * attempt
                if attempt < self.max_retries:
                    log.warning("文本模型调用失败（第 %d/%d 次，模型 %s）：%s，%.1fs 后重试",
                                attempt, self.max_retries, payload["model"],
                                self._brief(exc), wait)
                    time.sleep(wait)
        raise LLMError(f"文本模型调用失败：{self._brief(last_err)}") from last_err

    def _request_chat(self, payload: dict[str, Any], json_mode: bool) -> str:
        """在所有候选路径上尝试，成功后记住可用路径。"""
        errors: list[str] = []
        for path in self.chat_paths:
            body = dict(payload)
            try:
                resp = requests.post(path, headers=self._headers(), json=body,
                                     timeout=self.timeout)
                if resp.status_code in (401, 403):
                    raise AuthError(
                        f"文本服务密钥无效或被拒绝（HTTP {resp.status_code}）："
                        f"{truncate(resp.text, 300)}\n"
                        f"  当前密钥来源：{config.text_api_key_status()}\n"
                        "  请在 .env 的 DEEPSEEK_API_KEY 中填写正确的密钥。")
                # 个别实现不支持 response_format，去掉再试一次同一路径
                if resp.status_code >= 400 and json_mode and "response_format" in body:
                    body.pop("response_format", None)
                    resp = requests.post(path, headers=self._headers(), json=body,
                                         timeout=self.timeout)
                # 少数 OpenAI 兼容实现不认识 thinking 参数：去掉再试一次
                if resp.status_code == 400 and "thinking" in body:
                    body.pop("thinking", None)
                    resp = requests.post(path, headers=self._headers(), json=body,
                                         timeout=self.timeout)
                if resp.status_code in (404, 405, 415):
                    errors.append(f"{path}: HTTP {resp.status_code}")
                    continue
                if resp.status_code >= 400:
                    text = truncate(resp.text, 400)
                    if self._is_model_not_loaded(text):
                        raise LLMError(self._not_loaded_hint(text))
                    raise LLMError(f"HTTP {resp.status_code}: {text}")
                resp.raise_for_status()
                if self._good_path != path:
                    if self._good_path:
                        log.info("文本接口路径切换：%s -> %s", self._good_path, path)
                    else:
                        log.info("文本接口路径确认：%s", path)
                    self._good_path = path
                data = resp.json()
                self.last_usage = data.get("usage") or {}
                return self._extract_content(data)
            except AuthError:
                raise
            except requests.RequestException as exc:
                errors.append(f"{path}: {truncate(str(exc), 120)}")
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {truncate(str(exc), 120)}")
                continue
        raise LLMError("所有文本接口路径均失败：" + "；".join(errors[:3]))

    def _stream(self, payload: dict[str, Any],
                on_delta: Callable[[str], None]) -> str:
        """流式读取 SSE。"""
        payload = dict(payload)
        payload["stream"] = True
        chunks: list[str] = []
        last_error: Exception | None = None
        for path in self.chat_paths:
            try:
                with requests.post(path, headers=self._headers(), json=payload,
                                   timeout=self.timeout, stream=True) as resp:
                    if resp.status_code in (401, 403):
                        raise AuthError(f"密钥被拒绝（HTTP {resp.status_code}）")
                    if resp.status_code in (404, 405, 415):
                        last_error = LLMError(f"{path} HTTP {resp.status_code}")
                        continue
                    resp.raise_for_status()
                    if self._good_path != path:
                        self._good_path = path
                    for raw_line in resp.iter_lines(decode_unicode=False):
                        if not raw_line:
                            continue
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        body = line[5:].strip()
                        if body == "[DONE]":
                            break
                        try:
                            obj = json.loads(body)
                        except json.JSONDecodeError:
                            continue
                        delta = (((obj.get("choices") or [{}])[0].get("delta") or {})
                                 .get("content") or "")
                        if delta:
                            chunks.append(delta)
                            if on_delta:
                                try:
                                    on_delta("".join(chunks))
                                except Exception:  # noqa: BLE001
                                    pass
                    return "".join(chunks)
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                chunks.clear()
                continue
        raise LLMError(f"流式调用失败：{self._brief(last_error)}")

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        try:
            choice = (data.get("choices") or [{}])[0]
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"返回结构异常：{truncate(json.dumps(data, ensure_ascii=False), 300)}") from exc
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            # 少数实现返回分段内容
            content = "".join(part.get("text", "") for part in content
                              if isinstance(part, dict))
        if not content:
            content = choice.get("text") or ""
        if not content:
            reasoning = str(msg.get("reasoning_content") or "")
            hint = (f"（reasoning_content 有 {len(reasoning)} 字，"
                    f"可能是 max_tokens 被思考链吃光；"
                    f"把 config.TEXT_MAX_TOKENS 调大，或保持 "
                    f"TEXT_DISABLE_THINKING=True）") if reasoning else ""
            raise LLMError("模型返回内容为空" + hint)
        return clean_model_text(str(content))

    def chat_json(self,
                  messages: Sequence[dict[str, str]],
                  expect: str = "object",
                  temperature: float | None = None,
                  max_tokens: int | None = None,
                  retry_on_parse_fail: bool = True,
                  json_mode: bool = True,
                  **kwargs):
        """
        要求模型输出 JSON 并解析。

        json_mode=True 时会带上 response_format={"type":"json_object"}
        （DeepSeek 官方与多数 OpenAI 兼容服务都支持；不支持的会自动去掉重试）。
        解析失败时会追加一条"只输出 JSON"的提醒再试一次。

        **kwargs 用于吸收调用方多传的参数，保证接口对桩客户端与真实客户端都兼容。
        """
        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens,
                         json_mode=json_mode)
        parsed = parse_json_block(text, expect=expect)
        if parsed is None and retry_on_parse_fail:
            log.warning("JSON 解析失败，追加约束后重试一次")
            retry_messages = list(messages) + [
                {"role": "assistant", "content": truncate(text, 500)},
                {"role": "user",
                 "content": "上面的输出不是合法 JSON。请只输出合法 JSON，"
                            "不要任何解释文字、不要 markdown 代码块标记。"},
            ]
            text2 = self.chat(retry_messages, temperature=0.0, max_tokens=max_tokens,
                              json_mode=json_mode)
            parsed = parse_json_block(text2, expect=expect)
        return parsed

    # ------------------------------------------------------------------
    def text_model_status(self) -> dict[str, Any]:
        """读取服务端文本模型加载状态（/api/inference/status）。

        该端点只有 Unsloth 部署才有；vLLM 等服务上探测一次 404 后，
        本进程内不再重复探测（负缓存），避免状态检查每轮都打 404。
        """
        host = self.host
        if host in _UNSLOTH_STATUS_UNAVAILABLE:
            return {"error": _UNSLOTH_STATUS_UNAVAILABLE[host]}
        if not getattr(config, "TEXT_PROBE_UNSLOTH_STATUS", True):
            reason = "探测已关闭（config.TEXT_PROBE_UNSLOTH_STATUS=False）"
            _UNSLOTH_STATUS_UNAVAILABLE[host] = reason
            log.info("文本状态探测已关闭，跳过 %s/api/inference/status", host)
            return {"error": reason}
        try:
            r = requests.get(f"{host}/api/inference/status",
                              headers=self._headers(),
                              timeout=min(self.timeout, 20))
            if r.status_code < 400:
                return r.json() or {}
            if r.status_code in (404, 405):
                # 该端点不存在（非 Unsloth 原生服务，如 vLLM）：记住，不再重复探测
                reason = (f"HTTP {r.status_code}：服务端无 /api/inference/status 端点"
                          "（非 Unsloth 原生服务）")
                _UNSLOTH_STATUS_UNAVAILABLE[host] = reason
                log.info("文本状态端点不可用，本进程内不再探测：%s", host)
                return {"error": reason}
            return {"error": f"HTTP {r.status_code}: {truncate(r.text, 200)}"}
        except Exception as exc:  # noqa: BLE001
            return {"error": truncate(str(exc), 200)}

    def load_text_model(self, model_path: str | None = None,
                        on_progress=None) -> tuple[bool, str]:
        """
        请求本地服务端加载文本模型并轮询进度（仅本地部署有意义）。

        对应 /api/inference/load 与 /api/inference/load-progress。
        """
        if not self.is_local:
            return False, (f"当前文本服务是远程 API（{self.host}），无需加载模型。"
                           "若要用本地文本模型，请把 config.py 中的 "
                           "TEXT_API_HOST 改为本地服务地址。")
        path = (model_path or config.TEXT_MODEL_PATH or "").strip()
        if not path:
            return False, "未指定文本模型路径（请在界面填写，或设置 config.TEXT_MODEL_PATH）"
        try:
            r = requests.post(f"{self.host}/api/inference/load",
                              headers=self._headers(),
                              json={"model_path": path}, timeout=min(self.timeout, 300))
            if r.status_code in (401, 403):
                return False, f"密钥被拒绝（HTTP {r.status_code}）：{config.api_key_status()}"
            if r.status_code >= 400:
                return False, f"加载请求失败（HTTP {r.status_code}）：{truncate(r.text, 300)}"
        except Exception as exc:  # noqa: BLE001
            return False, f"加载请求异常：{truncate(str(exc), 200)}"

        deadline = time.time() + config.TEXT_LOAD_TIMEOUT
        last = ""
        while time.time() < deadline:
            try:
                pr = requests.get(f"{self.host}/api/inference/load-progress",
                                  headers=self._headers(),
                                  timeout=min(self.timeout, 20))
                if pr.status_code < 400:
                    node = pr.json() or {}
                    phase = node.get("phase") or ""
                    frac = node.get("fraction")
                    last = f"{phase} {float(frac) * 100:.0f}%" if isinstance(
                        frac, (int, float)) and frac else str(phase)
                    if on_progress:
                        try:
                            on_progress(node)
                        except Exception:  # noqa: BLE001
                            pass
            except Exception as exc:  # noqa: BLE001
                log.debug("读取文本模型加载进度失败：%s", exc)

            st = self.text_model_status()
            loaded = st.get("loaded") or []
            serving = st.get("serving") or []
            if loaded or serving:
                name = st.get("active_model") or st.get("model_identifier") or path
                return True, f"文本模型已加载：{name}"
            if st.get("memory_warning"):
                log.warning("服务端提示：%s", st["memory_warning"])
            time.sleep(config.TEXT_LOAD_POLL_INTERVAL)

        return False, f"等待文本模型加载超时（{config.TEXT_LOAD_TIMEOUT}s）。最后状态：{last}"

    # ------------------------------------------------------------------
    def health(self) -> tuple[bool, str]:
        """健康检查：验密钥 -> 看模型列表 -> 试一次极短对话。"""
        if config.TEXT_API_NEEDS_KEY and not self.key:
            return False, config.text_api_key_status()

        notes: list[str] = []
        probe_paths = [f"{self.base_url}/models"]
        if self.is_local:
            probe_paths.append(f"{self.host}/api/inference/models")
        for path in probe_paths:
            try:
                r = requests.get(path, headers=self._headers(),
                                 timeout=min(self.timeout, 20))
                if r.status_code in (401, 403):
                    return False, (f"密钥被拒绝（HTTP {r.status_code}）："
                                   f"{config.text_api_key_status()}")
                if r.status_code < 400:
                    n = 0
                    try:
                        data = r.json()
                        items = data.get("data") if isinstance(data, dict) else data
                        n = len(items) if isinstance(items, list) else 0
                    except Exception:  # noqa: BLE001
                        pass
                    notes.append("服务在线" + (f"（{n} 个模型）" if n else ""))
                    break
            except Exception as exc:  # noqa: BLE001
                notes.append(self._brief(exc))

        # 本地部署才需要检查"模型是否已加载"
        if self.is_local:
            st = self.text_model_status()
            if not st.get("error"):
                loaded = st.get("loaded") or []
                serving = st.get("serving") or []
                if not loaded and not serving:
                    hint = ("；".join(notes) + "；") if notes else ""
                    return True, (f"{hint}⚠️ 服务端尚未加载文本模型，"
                                  "请在「文本模型加载」面板中点击加载"
                                  f"（默认路径 {config.TEXT_MODEL_PATH}）")
                notes.append(f"已加载文本模型："
                             f"{st.get('active_model') or st.get('model_identifier')}")

        try:
            # 注意：max_tokens 不能太小。QYHT 等"推理模型"会先用掉一部分 token 生成
            # reasoning 字段，太小会导致 content 为空、误判为对话失败。1024 对"极短
            # 对话"探测足够，且对非推理模型无副作用（max_tokens 是上限不是目标）。
            self.chat([{"role": "user", "content": "回复：ok"}], max_tokens=1024,
                      temperature=0.0)
            prefix = ("；".join(notes) + "；") if notes else ""
            display = getattr(config, "TEXT_MODEL_DISPLAY", "") or self.model
            return True, f"{prefix}对话连通（{display} / {self.model}）"
        except AuthError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            prefix = ("；".join(notes) + "；") if notes else ""
            return False, f"{prefix}对话失败：{self._brief(exc)}"

    @staticmethod
    def _is_model_not_loaded(text: str) -> bool:
        low = (text or "").lower()
        hints = ("no model loaded", "not loaded", "call post /inference/load",
                 "model auto-switch", "未加载")
        return any(h in low for h in hints)

    @staticmethod
    def _not_loaded_hint(text: str) -> str:
        return ("服务端尚未加载文本模型。请在界面「文本模型加载」面板中点击加载"
                f"（默认路径 {config.TEXT_MODEL_PATH}），"
                "或在 Unsloth 的 Settings > API 中开启 Model auto-switch。\n"
                f"  服务端返回：{text}")

    @staticmethod
    def _brief(exc: Exception | None) -> str:
        if exc is None:
            return "未知错误"
        text = str(exc) or exc.__class__.__name__
        return truncate(text, 300)


def config_summary_keys() -> dict:
    """给界面用的密钥状态摘要（供 server.py 调用）。"""
    return {"image": config.api_key_status(), "text": config.text_api_key_status()}
