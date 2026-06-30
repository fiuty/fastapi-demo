"""
会话 Controller
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.pojo.conversation import ConversationCreate, ConversationUpdate, ConversationDetailVO, ConversationVO
from app.service.conversation_service import ConversationService

router = APIRouter(prefix="/api/conversations", tags=["会话管理"])


def get_conversation_service(db: Session = Depends(get_db)) -> ConversationService:
    return ConversationService(db)


@router.post("", summary="创建会话")
def create_conversation(
    form: ConversationCreate,
    service: ConversationService = Depends(get_conversation_service),
):
    conv = service.create_conversation(form)
    return {
        "code": 200,
        "message": "success",
        "data": ConversationVO.model_validate(conv).model_dump(),
    }


@router.get("", summary="分页查询会话")
def page_conversations(
    page: int = Query(default=1, ge=1, description="页码"),
    size: int = Query(default=10, ge=1, le=100, description="每页条数"),
    service: ConversationService = Depends(get_conversation_service),
):
    records, total = service.page_conversations(page=page, size=size)
    return {
        "code": 200,
        "message": "success",
        "data": {
            "records": [ConversationVO.model_validate(r).model_dump() for r in records],
            "total": total,
            "page": page,
            "size": size,
        },
    }


@router.get("/{conversation_id}", summary="查询会话详情(含消息)")
def get_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service),
):
    detail = service.get_conversation_detail(conversation_id)
    return {
        "code": 200,
        "message": "success",
        "data": detail.model_dump(),
    }


@router.put("/{conversation_id}", summary="更新会话标题")
def update_conversation(
    conversation_id: str,
    form: ConversationUpdate,
    service: ConversationService = Depends(get_conversation_service),
):
    conv = service.update_conversation(conversation_id, form)
    return {
        "code": 200,
        "message": "success",
        "data": ConversationVO.model_validate(conv).model_dump(),
    }


@router.delete("/{conversation_id}", summary="删除会话")
async def delete_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service),
):
    await service.delete_conversation(conversation_id)
    return {"code": 200, "message": "删除成功", "data": None}
