"""
学生 Controller
对应 Java 中的 @RestController + @RequestMapping
FastAPI 的 APIRouter 就是把多个相关接口归到一组, 统一加前缀
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.response import PageData, Response
from app.database import get_db
from app.pojo.student import StudentCreate, StudentQuery, StudentUpdate, StudentVO
from app.service.student_service import StudentService

router = APIRouter(
    prefix="/api/students",  # 相当于 @RequestMapping("/api/students")
    tags=["学生管理"],         # Swagger 分组标签
)


def get_service(db: Session = Depends(get_db)) -> StudentService:
    """依赖注入: Controller -> Service -> DAO, 一层层传递 Session"""
    return StudentService(db)


@router.get("", summary="分页查询学生", response_model=Response[PageData[StudentVO]])
def page_students(
    name: str | None = Query(default=None, description="姓名(模糊)"),
    student_no: str | None = Query(default=None, description="学号(精确)"),
    clazz: str | None = Query(default=None, description="班级(精确)"),
    page: int = Query(default=1, ge=1, description="页码"),
    size: int = Query(default=10, ge=1, le=100, description="每页条数"),
    service: StudentService = Depends(get_service),
):
    records, total = service.page_students(name, student_no, clazz, page, size)
    page_data = PageData[StudentVO].of(
        records=[StudentVO.model_validate(r) for r in records],
        total=total,
        page=page,
        size=size,
    )
    return Response[PageData[StudentVO]].ok(page_data)


@router.get("/{student_id}", summary="查询单个学生", response_model=Response[StudentVO])
def get_student(student_id: int, service: StudentService = Depends(get_service)):
    student = service.get_student(student_id)
    return Response[StudentVO].ok(StudentVO.model_validate(student))


@router.post("", summary="新增学生", response_model=Response[StudentVO], status_code=201)
def create_student(form: StudentCreate, service: StudentService = Depends(get_service)):
    student = service.create_student(form)
    return Response[StudentVO].ok(StudentVO.model_validate(student))


@router.put("/{student_id}", summary="修改学生", response_model=Response[StudentVO])
def update_student(
    student_id: int,
    form: StudentUpdate,
    service: StudentService = Depends(get_service),
):
    student = service.update_student(student_id, form)
    return Response[StudentVO].ok(StudentVO.model_validate(student))


@router.delete("/{student_id}", summary="删除学生", response_model=Response[None])
def delete_student(student_id: int, service: StudentService = Depends(get_service)):
    service.delete_student(student_id)
    return Response[None].ok(message="删除成功")
