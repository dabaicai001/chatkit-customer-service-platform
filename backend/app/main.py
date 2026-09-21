"""main.py:FastAPI 入口——通用客服平台 API。

端点:
- POST /support/chatkit                  ChatKit 协议入口(聊天/组件/听写)
- POST/GET /support/attachments/...      附件上传下载
- GET  /support/customer                 客户画像快照(经 MCP)
- GET  /support/bootstrap                前端引导配置(品牌/面板/欢迎语,来自 business.yaml)
- GET  /support/tools                    已启用工具与 Jev 路由参数(运维/调试)
- GET  /support/health                   健康检查(含 MCP 数据面状态)

启动即校验配置并连接 MCP 数据面,缺失/失败直接报错退出。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from chatkit.server import StreamingResult
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from starlette.responses import JSONResponse

from .config import ConfigurationError, load_config
from .server import CustomerServiceServer, create_chatkit_server

DEFAULT_THREAD_ID = "demo_default_thread"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 配置校验 + MCP 数据面连接(失败则启动中止)
    await customer_service_server.startup()
    yield
    await customer_service_server.shutdown()


app = FastAPI(title="通用客服平台 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    customer_service_server: CustomerServiceServer = create_chatkit_server(load_config())
except ConfigurationError as exc:
    # 配置缺失:拒绝启动,错误信息里写明了要配什么
    raise RuntimeError(str(exc)) from exc


def get_server() -> CustomerServiceServer:
    return customer_service_server


@app.post("/support/chatkit")
async def chatkit_endpoint(
    request: Request, server: CustomerServiceServer = Depends(get_server)
) -> Response:
    payload = await request.body()
    result = await server.process(payload, {"request": request})
    if isinstance(result, StreamingResult):
        return StreamingResponse(result, media_type="text/event-stream")
    if hasattr(result, "json"):
        return Response(content=result.json, media_type="application/json")
    return JSONResponse(result)


@app.api_route(
    "/support/attachments/{attachment_id}/upload",
    methods=["POST", "PUT"],
    name="upload_attachment",
)
async def upload_attachment(
    attachment_id: str,
    request: Request,
    server: CustomerServiceServer = Depends(get_server),
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "").lower()
    data: bytes | None = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Multipart 上传必须包含 file 字段。",
            )
        data = await file.read()  # type: ignore[call-arg]
    else:
        data = await request.body()

    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="附件内容不能为空。",
        )

    attachment = await server.attachment_uploader.write_file(
        attachment_id, data, {"request": request}
    )
    return attachment.model_dump()


@app.get(
    "/support/attachments/{attachment_id}/content",
    name="download_attachment",
)
async def download_attachment(
    attachment_id: str,
    request: Request,
    server: CustomerServiceServer = Depends(get_server),
) -> Response:
    attachment, data = await server.attachment_uploader.read_file(
        attachment_id, {"request": request}
    )
    return Response(
        content=data,
        media_type=attachment.mime_type,
        headers={
            "Cache-Control": "private, max-age=600",
            "Content-Disposition": f'inline; filename="{attachment.name}"',
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/support/customer")
async def customer_snapshot(
    thread_id: str | None = Query(None, description="ChatKit thread 标识"),
    server: CustomerServiceServer = Depends(get_server),
) -> dict[str, Any]:
    """客户画像快照(经 MCP 聚合上游数据);未识别客户时返回 null。"""

    key = thread_id or DEFAULT_THREAD_ID
    customer_id = server.sessions.customer_id(key)
    if not customer_id:
        return {"customer": None}
    profile = await server.gateway.load_profile(customer_id)
    return {"customer": profile.to_dict()}


@app.get("/support/bootstrap")
async def bootstrap_config(
    server: CustomerServiceServer = Depends(get_server),
) -> dict[str, Any]:
    """前端引导配置:换一家公司的客服 = 改 business.yaml,前端无需改代码。"""

    config = server.config
    return {
        "company": {"name": config.company_name, "industry": config.industry},
        "customer_service": {
            "name": config.agent_name,
            "language": config.language,
            "greeting": config.greeting,
            "composer_placeholder": config.composer_placeholder,
        },
        "panels": config.panels,
        "agents": [
            spec.to_dict() for spec in (server.jev.agents.get(name) for name in server.jev.agents.names())
        ],
        "default_agent": server.jev.agents.default_name,
    }


@app.get("/support/tools")
async def tools_debug(
    server: CustomerServiceServer = Depends(get_server),
) -> dict[str, Any]:
    """已启用工具、Jev 路由参数与 MCP 映射(运维/调试)。"""

    config = server.config
    return {
        "enabled_tools": server.tools.enabled_names(),
        "tool_schemas": server.tools.schemas(),
        "agents": [
            spec.to_dict() for spec in (server.jev.agents.get(name) for name in server.jev.agents.names())
        ],
        "default_agent": server.jev.agents.default_name,
        "jev": {
            "confidence": {
                "high": config.get("jev.confidence.high"),
                "low": config.get("jev.confidence.low"),
            },
            "fallback_action": config.get("jev.fallback_action"),
            "emotion_routing": config.get("jev.emotion_routing"),
            "models": {
                slot: {
                    "provider": config.model_config(slot)["provider"],
                    "model": config.model_config(slot)["model"],
                }
                for slot in ("decision", "chat", "title")
            },
        },
        "mcp": {
            "servers": server.mcp.server_names,
            "tool_mapping": {
                name: server.mcp.mapping_for(name)
                for name in server.tools.enabled_names()
                if server.mcp.mapping_for(name)
            },
        },
    }


@app.get("/support/health")
async def health_check(
    server: CustomerServiceServer = Depends(get_server),
) -> dict[str, Any]:
    return {
        "status": "healthy",
        "mcp": await server.mcp.health(),
    }
