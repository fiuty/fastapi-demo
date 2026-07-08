"""
AgentScope 流式对话 Controller
"""
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.pojo.message import ChatRequest, ChatInteractiveRequest, InterruptRequest
from app.service.agentscope.chat_service import ChatService

logger = logging.getLogger("agentscope")

router = APIRouter(prefix="/api/agentscope", tags=["AgentScope 对话"])


def get_chat_service(db: Session = Depends(get_db)) -> ChatService:
    return ChatService(db)


@router.post("/chat/stream", summary="流式对话(SSE)")
async def chat_stream(
    request: ChatRequest,
    chat_service: ChatService = Depends(get_chat_service),
):
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for sse_event in chat_service.chat_stream(
                user_message=request.message,
                conversation_id=request.conversation_id,
            ):
                event_type = sse_event["event"]
                data_str = json.dumps(sse_event["data"], ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data_str}\n\n"
        except Exception as e:
            logger.exception("流式对话异常")
            error_data = json.dumps({"message": str(e)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/interrupt", summary="中断对话(运行中或暂停中)")
async def chat_interrupt(
    request: InterruptRequest,
    chat_service: ChatService = Depends(get_chat_service),
):
    """中断指定会话的智能体回复。

    - 若会话正在流式回复: 取消运行任务, Agent 清理上下文后产出 INTERRUPTED 结束事件,
      原流会收到 ``reply_end`` (finished_reason=interrupted) 后关闭。
    - 若会话处于暂停(等待工具确认/外部执行): 通过 UserInterruptEvent 清理待处理工具调用。
    - 若会话无活跃回复: 返回 noop。

    中断后 state 与(部分)消息已持久化, 携带 conversation_id 再次调用
    ``/chat/stream`` 即可恢复会话, 历史消息不丢失。
    """
    result = await chat_service.interrupt(request.conversation_id)
    return result


@router.post("/chat/stream/interactive", summary="交互式流式对话(SSE, 含工具权限确认)")
async def chat_stream_interactive(
    request: ChatInteractiveRequest,
    chat_service: ChatService = Depends(get_chat_service),
):
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            confirm_results = None
            if request.confirm_results:
                confirm_results = [
                    {
                        "tool_call_id": r.tool_call_id,
                        "tool_call_name": r.tool_call_name,
                        "confirmed": r.confirmed,
                    }
                    for r in request.confirm_results
                ]

            async for sse_event in chat_service.chat_stream_interactive(
                user_message=request.message,
                confirm_results=confirm_results,
                conversation_id=request.conversation_id,
            ):
                event_type = sse_event["event"]
                data_str = json.dumps(sse_event["data"], ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data_str}\n\n"
        except Exception as e:
            logger.exception("交互式流式对话异常")
            error_data = json.dumps({"message": str(e)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
