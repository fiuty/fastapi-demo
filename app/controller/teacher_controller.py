"""
教师 Controller
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.response import PageData, Response
from app.database import get_db
from app.pojo.teacher import TeacherCreate, TeacherUpdate, TeacherVO
from app.service.teacher_service import TeacherService

router = APIRouter(
    prefix="/api/teachers",
    tags=["教师管理"],
)


def get_service(db: Session = Depends(get_db)) -> TeacherService:
    return TeacherService(db)


@router.get("", summary="分页查询教师", response_model=Response[PageData[TeacherVO]])
def page_teachers(
    name: str | None = Query(default=None, description="姓名(模糊)"),
    teacher_no: str | None = Query(default=None, description="工号(精确)"),
    subject: str | None = Query(default=None, description="学科(精确)"),
    page: int = Query(default=1, ge=1, description="页码"),
    size: int = Query(default=10, ge=1, le=100, description="每页条数"),
    service: TeacherService = Depends(get_service),
):
    records, total = service.page_teachers(name, teacher_no, subject, page, size)
    page_data = PageData[TeacherVO].of(
        records=[TeacherVO.model_validate(r) for r in records],
        total=total,
        page=page,
        size=size,
    )
    return Response[PageData[TeacherVO]].ok(page_data)


@router.get("/{teacher_id}", summary="查询单个教师", response_model=Response[TeacherVO])
def get_teacher(teacher_id: int, service: TeacherService = Depends(get_service)):
    teacher = service.get_teacher(teacher_id)
    return Response[TeacherVO].ok(TeacherVO.model_validate(teacher))


@router.post("", summary="新增教师", response_model=Response[TeacherVO], status_code=201)
def create_teacher(form: TeacherCreate, service: TeacherService = Depends(get_service)):
    teacher = service.create_teacher(form)
    return Response[TeacherVO].ok(TeacherVO.model_validate(teacher))


@router.put("/{teacher_id}", summary="修改教师", response_model=Response[TeacherVO])
def update_teacher(
    teacher_id: int,
    form: TeacherUpdate,
    service: TeacherService = Depends(get_service),
):
    teacher = service.update_teacher(teacher_id, form)
    return Response[TeacherVO].ok(TeacherVO.model_validate(teacher))


@router.delete("/{teacher_id}", summary="删除教师", response_model=Response[None])
def delete_teacher(teacher_id: int, service: TeacherService = Depends(get_service)):
    service.delete_teacher(teacher_id)
    return Response[None].ok(message="删除成功")
