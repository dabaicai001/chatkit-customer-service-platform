"""ai/qwen.py:Qwen 生成层——负责"把话说好"的人。

Jev 决策、工具执行、RAG 检索的结果在这里汇聚,由 Qwen 生成最终客服话术,
以流式(SSE)方式逐段产出。通过 business.yaml 的 ``models.chat`` 槽位接入
(独立环境变量 QWEN_PROVIDER / QWEN_BASE_URL / QWEN_API_KEY / QWEN_MODEL)。

未配置或调用失败直接抛错,不做模板兜底。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from ..config import BusinessConfig
from ..core.customer import CustomerProfile

logger = logging.getLogger(__name__)


class GenerationError(RuntimeError):
    """Qwen 生成失败(配置缺失 / 模型调用失败)。"""


@dataclass
class ComposeContext:
    """一次生成所需的全部上下文。"""

    message: str
    decision_summary: str = ""
    profile: Optional[CustomerProfile] = None
    tool_result: Optional[Dict[str, Any]] = None
    tool_error: Optional[str] = None
    passages: List[Dict[str, Any]] | None = None
    awaiting_confirmation: Optional[str] = None  # 确认卡片提示语
    handoff: Optional[Dict[str, Any]] = None
    history: str = ""


_QWEN_SYSTEM_TEMPLATE = """你是{agent_name},{company_name}的在线客服。你的任务是把已经查证好的结果
用自然、亲切、简洁的中文说给客户听。

硬性规则:
- 只能基于下方提供的「已查证信息」回答,禁止编造订单号、金额、政策细节。
- 「已查证信息」中没有的内容,礼貌说明并建议客户提供更多信息或转人工。
- 每次回复 2-4 句话,除非客户要求详细说明。
- 客户情绪激动时,先安抚,再给方案。
- 如果信息里包含确认提示(待确认动作),引导客户点击卡片上的按钮确认或取消。
- 如果已转入人工排队,告知排队位置与预计等待,请客户稍候。

【客户资料】
{profile_block}

【本次决策(Jev)】
{decision_block}

【已查证信息(工具/知识库结果)】
{evidence_block}

【对话历史】
{history_block}
"""


class QwenComposer:
    """Qwen 流式话术生成器。"""

    def __init__(self, config: BusinessConfig) -> None:
        self._config = config
        self._cfg = config.require_model("chat")  # 缺失直接 ConfigurationError
        self._title_cfg = config.model_config("title")
        logger.info(
            "Qwen 生成层就绪(provider=%s, model=%s)。",
            self._cfg["provider"],
            self._cfg["model"],
        )

    # ------------------------------------------------------------- 流式生成
    async def compose_stream(self, ctx: ComposeContext) -> AsyncIterator[str]:
        """流式生成客服话术,逐段 yield 文本。"""

        payload = {
            "model": self._cfg["model"],
            "temperature": self._cfg["temperature"],
            "stream": True,
            "messages": [
                {"role": "system", "content": self._build_system_prompt(ctx)},
                {"role": "user", "content": self._build_user_prompt(ctx)},
            ],
        }
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
                        chunk = self._parse_sse_line(line)
                        if chunk:
                            yield chunk
        except httpx.HTTPError as exc:
            raise GenerationError(f"Qwen 生成失败:{exc}") from exc

    async def compose(self, ctx: ComposeContext) -> str:
        """非流式便捷接口(收集完整结果)。"""

        parts = [chunk async for chunk in self.compose_stream(ctx)]
        text = "".join(parts).strip()
        if not text:
            raise GenerationError("Qwen 返回了空内容。")
        return text

    # ------------------------------------------------------------- 标题生成
    async def generate_title(self, first_user_message: str) -> str:
        """基于首条用户消息生成会话标题(2-8 个字)。"""

        cfg = self._title_cfg
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
            title = str(data["choices"][0]["message"]["content"]).strip()
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            raise GenerationError(f"标题生成失败:{exc}") from exc
        title = title.strip("\"'`。.!！?？, ,,，、")
        return title[:20] or "客户会话"

    # ------------------------------------------------------------- 提示词组装
    def _build_system_prompt(self, ctx: ComposeContext) -> str:
        profile_block = ctx.profile.summary_text() if ctx.profile else "尚未识别客户身份。"
        history_block = ctx.history or "(暂无历史)"
        return _QWEN_SYSTEM_TEMPLATE.format(
            agent_name=self._config.agent_name,
            company_name=self._config.company_name,
            profile_block=profile_block,
            decision_block=ctx.decision_summary or "(无)",
            evidence_block=self._render_evidence(ctx),
            history_block=history_block,
        )

    @staticmethod
    def _build_user_prompt(ctx: ComposeContext) -> str:
        parts: List[str] = [f"客户最新消息:{ctx.message}"]
        if ctx.awaiting_confirmation:
            parts.append(f"待客户确认:{ctx.awaiting_confirmation}")
        if ctx.handoff:
            parts.append(
                "转人工信息:"
                f"排队第 {ctx.handoff.get('queue_position', 1)} 位,"
                f"预计等待 {ctx.handoff.get('estimated_wait', '稍等')}。"
            )
        if ctx.tool_error:
            parts.append(f"工具执行出现问题:{ctx.tool_error}(请向客户说明并引导补充信息)")
        parts.append("请生成回复:")
        return "\n".join(parts)

    @staticmethod
    def _render_evidence(ctx: ComposeContext) -> str:
        blocks: List[str] = []
        if ctx.awaiting_confirmation:
            blocks.append(f"[待确认动作] {ctx.awaiting_confirmation}")
        if ctx.tool_error:
            blocks.append(f"[工具异常] {ctx.tool_error}")
        if ctx.tool_result:
            message = ctx.tool_result.get("result", "")
            data = ctx.tool_result.get("data", {})
            blocks.append(f"[工具结果] {message}")
            if data:
                blocks.append(f"[结构化数据] {json.dumps(data, ensure_ascii=False)[:1500]}")
        if ctx.passages:
            for passage in ctx.passages:
                blocks.append(
                    f"[知识库·{passage.get('title', '')}] {passage.get('content', '')}"
                )
        if not blocks:
            blocks.append("(本次无需查证,直接对话)")
        return "\n".join(blocks)

    # ------------------------------------------------------------- SSE 解析
    @staticmethod
    def _parse_sse_line(line: str) -> str:
        line = (line or "").strip()
        if not line.startswith("data:"):
            return ""
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            return ""
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return ""
        try:
            choices = event.get("choices") or []
            if not choices:
                return ""
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            return str(content) if content else ""
        except (AttributeError, IndexError, TypeError):
            return ""
