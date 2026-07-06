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
from app.pojo.message import ChatRequest, ChatInteractiveRequest
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
