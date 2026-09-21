"""tests/mock_llm.py:OpenAI 兼容 mock 服务(Jev 决策 / Qwen 流式生成 / 标题)。

让测试不依赖任何真实模型端点:按用户消息关键词确定性地返回
Jev 决策 JSON、Qwen 流式文本与会话标题。
"""

from __future__ import annotations

import json
import re
import threading

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

_ORDER_ID_RE = re.compile(r"#?\b(\d{8,})\b")


class ChatRequest(BaseModel):
    model: str = ""
    messages: list = []
    stream: bool = False
    temperature: float = 0.0


def _jev_decision(user_message: str) -> dict:
    message = user_message.lower()
    slots: dict = {"query": user_message}
    match = _ORDER_ID_RE.search(user_message)
    if match:
        slots["order_id"] = match.group(1)

    if any(w in message for w in ("垃圾", "气死", "坑人", "太差")):
        return {
            "intent": "complaint",
            "confidence": 0.9,
            "emotion": "angry",
            "need_customer_lookup": True,
            "need_rag": False,
            "need_tool": False,
            "need_human": True,
            "action": "transfer_to_human",
            "slots": slots,
            "reason": "用户情绪愤怒,直接转人工",
        }
    if "转人工" in message or "人工客服" in message:
        return {
            "intent": "human_request",
            "confidence": 0.97,
            "emotion": "normal",
            "need_customer_lookup": False,
            "need_rag": False,
            "need_tool": True,
            "need_human": True,
            "action": "transfer_to_human",
            "slots": slots,
            "reason": "用户明确要求人工",
        }
    # 身份识别(本地演示:说"我是李明/手机号"即可点亮侧栏客户档案)
    if any(w in message for w in ("我是", "手机号", "李明", "查客户", "客户")) and "订单" not in message:
        phone_match = re.search(r"1[3-9]\d{9}", user_message)
        keyword = "李明" if "李明" in message else (phone_match.group(0) if phone_match else "")
        identity_slots = {"query": user_message}
        if keyword:
            identity_slots["keyword"] = keyword
        return {
            "intent": "customer_identify",
            "confidence": 0.95,
            "emotion": "normal",
            "need_customer_lookup": True,
            "need_rag": False,
            "need_tool": True,
            "need_human": False,
            "action": "search_customer",
            "slots": identity_slots,
            "reason": "用户在表明身份,先检索客户",
        }
    if "退款" in message:
        return {
            "intent": "refund_request",
            "confidence": 0.93,
            "emotion": "normal",
            "need_customer_lookup": True,
            "need_rag": False,
            "need_tool": True,
            "need_human": False,
            "action": "refund_order",
            "slots": slots,
            "reason": "用户申请退款",
        }
    if "取消" in message:
        return {
            "intent": "cancel_order",
            "confidence": 0.91,
            "emotion": "normal",
            "need_customer_lookup": True,
            "need_rag": False,
            "need_tool": True,
            "need_human": False,
            "action": "cancel_order",
            "slots": slots,
            "reason": "用户要取消订单",
        }
    if any(w in message for w in ("订单", "发货", "物流", "到哪", "快递")):
        return {
            "intent": "order_query",
            "confidence": 0.95,
            "emotion": "normal",
            "need_customer_lookup": True,
            "need_rag": False,
            "need_tool": True,
            "need_human": False,
            "action": "get_order",
            "slots": slots,
            "reason": "用户查询订单物流",
        }
    if any(w in message for w in ("政策", "怎么", "如何", "保修", "发票")):
        return {
            "intent": "knowledge_query",
            "confidence": 0.88,
            "emotion": "normal",
            "need_customer_lookup": False,
            "need_rag": True,
            "need_tool": True,
            "need_human": False,
            "action": "query_knowledge",
            "slots": slots,
            "reason": "用户咨询通用政策",
        }
    if "投诉" in message or "工单" in message:
        return {
            "intent": "ticket_create",
            "confidence": 0.9,
            "emotion": "normal",
            "need_customer_lookup": True,
            "need_rag": False,
            "need_tool": True,
            "need_human": False,
            "action": "create_ticket",
            "slots": {**slots, "subject": "客户投诉", "description": user_message},
            "reason": "用户要建工单",
        }
    if "商品" in message or "价格" in message:
        return {
            "intent": "product_query",
            "confidence": 0.89,
            "emotion": "normal",
            "need_customer_lookup": False,
            "need_rag": False,
            "need_tool": True,
            "need_human": False,
            "action": "get_product",
            "slots": {**slots, "keyword": "耳机"},
            "reason": "用户咨询商品",
        }
    return {
        "intent": "greet",
        "confidence": 0.6,
        "emotion": "normal",
        "need_customer_lookup": False,
        "need_rag": False,
        "need_tool": False,
        "need_human": False,
        "action": "none",
        "slots": slots,
        "reason": "闲聊/打招呼",
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    system = next(
        (m.get("content", "") for m in request.messages if m.get("role") == "system"), ""
    )
    user = next(
        (m.get("content", "") for m in reversed(request.messages) if m.get("role") == "user"), ""
    )

    # Jev 决策请求
    if "意图决策引擎" in system:
        decision = _jev_decision(user.split("用户最新消息:")[-1])
        return {
            "id": "chatcmpl-jevmock",
            "object": "chat.completion",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)}, "finish_reason": "stop"}
            ],
        }

    # 标题生成请求
    if "会话标题" in system:
        return {
            "id": "chatcmpl-titlemock",
            "object": "chat.completion",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "订单物流咨询"}, "finish_reason": "stop"}
            ],
        }

    # Qwen 生成请求(流式)
    # 演示用:把 system 里的「工具结果/待确认」原样回显,模拟真实 grounding 话术
    reply = f"【mock 客服回复】已收到:{user[-60:]}"
    if "已查证信息" in system:
        evidence = ""
        for line in system.splitlines():
            if line.startswith("[工具结果]"):
                evidence = line[len("[工具结果]"):].strip()
            elif line.startswith("[待确认动作]"):
                evidence = line[len("[待确认动作]"):].strip()
        reply = (
            f"【mock 客服回复】{evidence}"
            if evidence
            else "【mock 客服回复】根据已查证信息,您的诉求已记录并处理。"
        )
    chunks = [reply[i : i + 8] for i in range(0, len(reply), 8)]
    if not request.stream:
        return {
            "id": "chatcmpl-qwenmock",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
        }

    from fastapi.responses import StreamingResponse

    def event_stream():
        for index, chunk in enumerate(chunks):
            payload = {
                "id": "chatcmpl-qwenmock",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def start_mock_llm(port: int = 0) -> tuple[str, threading.Thread, uvicorn.Server]:
    """在后台线程启动 mock 服务,返回 (base_url, thread, server)。"""

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        import time

        time.sleep(0.05)
    actual_port = server.servers[0].sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{actual_port}/v1", thread, server


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="mock OpenAI 兼容服务")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
