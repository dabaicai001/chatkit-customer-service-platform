"""ai/qwen.py:chat 槽位公共能力——思维链过滤与会话标题。

话术生成与「模型直连 MCP」的工具调用循环已迁至 ai/mcp_agent.py;
这里只保留与具体请求无关的公共件:

- ``_ReasoningFilter`` / ``_strip_reasoning``:推理型模型(MiniMax-M3 等)
  会把思维链混进 content,流式用状态机过滤、非流式用整块剥离;
- ``generate_title``:会话标题生成(复用 models.title 槽位);
- ``GenerationError``:生成失败显式抛错(不静默降级)。
"""

from __future__ import annotations

import logging
import re
from typing import List

import httpx

from ..config import BusinessConfig

logger = logging.getLogger(__name__)

# 推理型模型(MiniMax-M3 等)可能把思维链直接混在 content 里流出来。
# reasoning_content 独立字段的模型天然安全(本层只读 content);这里兜底
# 处理 content 内联 <think>... 的场景——流式用状态机,非流式用整块正则。
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_REASONING_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_reasoning(text: str) -> str:
    """去掉非流式文本里的思维链块(含未闭合时 <think> 之后的全部内容)。"""

    cleaned = _REASONING_BLOCK_RE.sub("", text or "")
    if _THINK_OPEN in cleaned:
        cleaned = cleaned.split(_THINK_OPEN, 1)[0]
    return cleaned.strip()


def _tag_prefix_suffix(text: str, tag: str) -> str:
    """text 的最长后缀,且是 tag 的真前缀(下一 chunk 可能拼齐标签);没有则 ""。"""

    for size in range(min(len(tag) - 1, len(text)), 0, -1):
        if text[-size:] == tag[:size]:
            return text[-size:]
    return ""


class _ReasoningFilter:
    """流式思维链过滤器(内容先入状态机,只放行确认不是思维链的部分)。

    `<think>`/`</think>` 可能跨 chunk 断裂:普通文本只把"可能拼成标签的前缀"
    扣在缓冲区,其余立即放行;思维链中只保留可能构成 close 的后缀,其余丢弃;
    未闭合的思维链在流末(flush)整体丢弃。
    """

    def __init__(self) -> None:
        self._in_think = False
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._buffer += chunk
        out: List[str] = []
        while self._buffer:
            if self._in_think:
                idx = self._buffer.find(_THINK_CLOSE)
                if idx == -1:
                    # 只留可能构成 close 的后缀,其余是思维链内容,丢弃
                    self._buffer = _tag_prefix_suffix(self._buffer, _THINK_CLOSE)
                    break
                self._buffer = self._buffer[idx + len(_THINK_CLOSE) :]
                self._in_think = False
                continue
            idx = self._buffer.find(_THINK_OPEN)
            if idx == -1:
                hold = _tag_prefix_suffix(self._buffer, _THINK_OPEN)
                emit = self._buffer[: len(self._buffer) - len(hold)]
                self._buffer = hold
                out.append(emit)
                break
            out.append(self._buffer[:idx])
            self._buffer = self._buffer[idx + len(_THINK_OPEN) :]
            self._in_think = True
        return "".join(out)

    def flush(self) -> str:
        if self._in_think:
            self._buffer = ""
            return ""
        tail, self._buffer = self._buffer, ""
        return tail


class GenerationError(RuntimeError):
    """生成模型调用失败(配置缺失 / 请求失败 / 输出不可用)。"""


async def generate_title(config: BusinessConfig, first_user_message: str) -> str:
    """基于首条用户消息生成会话标题(2-8 个字);失败抛 GenerationError。"""

    cfg = config.model_config("title")
    if not str(cfg.get("base_url", "")).strip() or not str(cfg.get("api_key", "")).strip():
        raise GenerationError("标题生成模型未配置(models.title 槽位)。")
    payload = {
        "model": cfg["model"],
        "temperature": 0.3,
        "messages": [
            {
                "role": "system",
                "content": "根据用户的第一条消息,生成一个简短的客服会话标题。"
                "只输出标题本身,2-8 个汉字,不要标点、不要引号、不要解释。",
            },
            {"role": "user", "content": first_user_message[:200]},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=float(cfg.get("timeout_seconds", 30) or 30)) as client:
            response = await client.post(
                f"{str(cfg['base_url']).rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
            )
            response.raise_for_status()
            data = response.json()
        title = _strip_reasoning(str(data["choices"][0]["message"]["content"]))
    except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
        raise GenerationError(f"标题生成失败:{exc}") from exc
    title = title.strip("\"'`。.!！?？, ,,，、")
    return title[:20] or "客户会话"
