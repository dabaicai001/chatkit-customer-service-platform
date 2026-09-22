"""ai/mcp_agent.py:模型直连 MCP 的智能体(function calling)。

Jev 只回答「建议调用哪个函数」(见 ai/jev.py 的 action 选择题);真正发起
调用的是本模块:话术模型(chat 槽位,OpenAI 兼容 tools 协议)输出
tool_calls(函数名 + 参数),由**代码侧**经 ToolRegistry 执行——

- pydantic 强校验入参;
- 剥离模型给出的 customer_id(身份只来自会话绑定,防越权);
- MCP 数据面由 McpClientManager 统一请求(模型碰不到 MCP 连接)。

工具结果以 tool 消息回灌,模型基于事实继续,直到产出最终话术。
流式:每轮 content 实时吐字;tool_calls 在 SSE 里按 index 增量累积。

注意:chat 槽位端点必须支持 OpenAI 兼容 function calling(tools 参数);
不支持时按约定显式报错(GenerationError),不静默降级。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Sequence

import httpx

from ..config import BusinessConfig
from .qwen import GenerationError, _ReasoningFilter

logger = logging.getLogger(__name__)

#: 工具结果回灌给模型的最大字符数(防止超长上下文打爆请求)
MAX_TOOL_RESULT_CHARS = 4000


@dataclass
class TextDelta:
    """一个 content 增量(已过滤思维链)。"""

    text: str


@dataclass
class ToolCallsReady:
    """本轮 SSE 结束,模型要求调用工具(携带完整累积结果)。"""

    calls: List["ToolCallRequest"]


@dataclass
class ToolCallRequest:
    """一次模型请求的工具调用。"""

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> Dict[str, Any]:
        """回灌 messages 时的 assistant.tool_calls 条目。"""

        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


class _ToolCallAccumulator:
    """按 SSE 增量累积 tool_calls(index → id/name/arguments 片段拼接)。"""

    def __init__(self) -> None:
        self._by_index: Dict[int, Dict[str, str]] = {}

    def feed(self, deltas: Sequence[Dict[str, Any]]) -> None:
        for delta in deltas:
            index = delta.get("index", 0)
            try:
                index = int(index)
            except (TypeError, ValueError):
                index = 0
            entry = self._by_index.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if delta.get("id"):
                entry["id"] = str(delta["id"])
            function = delta.get("function") or {}
            if function.get("name"):
                entry["name"] += str(function["name"])
            if function.get("arguments"):
                entry["arguments"] += str(function["arguments"])

    def has_calls(self) -> bool:
        return bool(self._by_index)

    def build(self) -> List[ToolCallRequest]:
        calls: List[ToolCallRequest] = []
        for index in sorted(self._by_index):
            entry = self._by_index[index]
            calls.append(
                ToolCallRequest(
                    id=entry["id"] or f"call_{index}",
                    name=entry["name"],
                    arguments=_parse_arguments(entry["arguments"]),
                )
            )
        return calls


def _parse_arguments(raw: str) -> Dict[str, Any]:
    """解析模型给出的 arguments JSON;残缺不当成错误,交给 pydantic 校验报错。"""

    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("模型 tool_call arguments 不是合法 JSON,原样交给校验层:%r", text[:200])
        return {"_raw_arguments": text}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


class McpToolAgent:
    """chat 槽位的工具调用循环(流式 + tools)。"""

    def __init__(self, config: BusinessConfig) -> None:
        self._config = config
        self._cfg = config.require_model("chat")
        self._max_rounds = max(1, int(config.get("agent.max_tool_rounds", 4) or 4))
        logger.info(
            "模型直连 MCP 智能体就绪(model=%s, 最大工具轮数=%d)。",
            self._cfg["model"],
            self._max_rounds,
        )

    @property
    def max_rounds(self) -> int:
        """单条消息允许的最大工具调用轮数(防无限循环)。"""

        return self._max_rounds

    async def stream_round(
        self,
        messages: List[Dict[str, Any]],
        tools: Sequence[Dict[str, Any]] | None,
    ) -> AsyncIterator[TextDelta | ToolCallsReady]:
        """跑一轮对话:content 实时 yield;若有 tool_calls,末尾 yield ToolCallsReady。"""

        payload: Dict[str, Any] = {
            "model": self._cfg["model"],
            "temperature": self._cfg["temperature"],
            "stream": True,
            "messages": messages,
        }
        if tools:
            payload["tools"] = list(tools)

        accumulator = _ToolCallAccumulator()
        reasoning = _ReasoningFilter()
        try:
            async with httpx.AsyncClient(timeout=self._cfg["timeout_seconds"]) as client:
                async with client.stream(
                    "POST",
                    f"{self._cfg['base_url'].rstrip('/')}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._cfg['api_key']}",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        content, tool_deltas = self._parse_sse_line(line)
                        text = reasoning.feed(content)
                        if text:
                            yield TextDelta(text)
                        accumulator.feed(tool_deltas)
                    tail = reasoning.flush()
                    if tail:
                        yield TextDelta(tail)
        except httpx.HTTPError as exc:
            raise GenerationError(f"模型调用失败:{exc}") from exc

        if accumulator.has_calls():
            yield ToolCallsReady(accumulator.build())

    # ------------------------------------------------------------- SSE 解析
    @staticmethod
    def _parse_sse_line(line: str) -> tuple[str, List[Dict[str, Any]]]:
        """一行 SSE → (content 文本, tool_calls 增量列表)。"""

        line = (line or "").strip()
        if not line.startswith("data:"):
            return "", []
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            return "", []
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return "", []
        try:
            choice = (event.get("choices") or [])[0]
            delta = choice.get("delta") or {}
        except (AttributeError, IndexError):
            return "", []
        content = delta.get("content")
        tool_calls = delta.get("tool_calls") or []
        return (
            str(content) if content else "",
            [tc for tc in tool_calls if isinstance(tc, dict)],
        )
