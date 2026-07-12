"""
教师业务层
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.common.exception import BizException
from app.dao.teacher_dao import TeacherDAO
from app.pojo.teacher import Teacher
from app.model.teacher import TeacherCreate, TeacherUpdate


class TeacherService:
    def __init__(self, db: Session):
        self.db = db
        self.dao = TeacherDAO(db)

    def create_teacher(self, form: TeacherCreate) -> Teacher:
        if self.dao.get_by_teacher_no(form.teacher_no):
            raise BizException(code=400, message=f"工号 {form.teacher_no} 已存在")

        teacher = Teacher(**form.model_dump())
        return self.dao.insert(teacher)

    def get_teacher(self, teacher_id: int) -> Teacher:
        teacher = self.dao.get_by_id(teacher_id)
        if teacher is None:
            raise BizException(code=404, message=f"教师(id={teacher_id})不存在")
        return teacher

    def page_teachers(
        self,
        name: Optional[str],
        teacher_no: Optional[str],
        subject: Optional[str],
        page: int,
        size: int,
    ) -> tuple[list[Teacher], int]:
        return self.dao.page_query(name, teacher_no, subject, page, size)

    def update_teacher(self, teacher_id: int, form: TeacherUpdate) -> Teacher:
        teacher = self.get_teacher(teacher_id)

        update_data = form.model_dump(exclude_unset=True)
        if not update_data:
            return teacher

        if "teacher_no" in update_data and update_data["teacher_no"] != teacher.teacher_no:
            if self.dao.get_by_teacher_no(update_data["teacher_no"]):
                raise BizException(code=400, message=f"工号 {update_data['teacher_no']} 已被占用")

        for key, value in update_data.items():
            setattr(teacher, key, value)
        return self.dao.update(teacher)

    def delete_teacher(self, teacher_id: int) -> None:
        if not self.dao.delete_by_id(teacher_id):
            raise BizException(code=404, message=f"教师(id={teacher_id})不存在")
