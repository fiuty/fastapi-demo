"""
学生业务层
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.common.exception import BizException
from app.dao.student_dao import StudentDAO
from app.model.student import Student
from app.pojo.student import StudentCreate, StudentUpdate


class StudentService:
    """
    @Service
    public class StudentServiceImpl extends ServiceImpl<StudentMapper, Student>
            implements IStudentService {
        ...
    }
    """

    def __init__(self, db: Session):
        self.db = db
        self.dao = StudentDAO(db)

    # ---------- C: Create ----------
    def create_student(self, form: StudentCreate) -> Student:
        """新增学生"""
        # 业务校验: 学号唯一
        if self.dao.get_by_student_no(form.student_no):
            raise BizException(code=400, message=f"学号 {form.student_no} 已存在")

        student = Student(**form.model_dump())
        return self.dao.insert(student)

    # ---------- R: Read ----------
    def get_student(self, student_id: int) -> Student:
        """查询单个学生 (不存在时抛业务异常)"""
        student = self.dao.get_by_id(student_id)
        if student is None:
            raise BizException(code=404, message=f"学生(id={student_id})不存在")
        return student

    def page_students(
        self,
        name: Optional[str],
        student_no: Optional[str],
        clazz: Optional[str],
        page: int,
        size: int,
    ) -> tuple[list[Student], int]:
        """分页查询学生"""
        return self.dao.page_query(name, student_no, clazz, page, size)

    # ---------- U: Update ----------
    def update_student(self, student_id: int, form: StudentUpdate) -> Student:
        """修改学生 (局部更新)"""
        student = self.get_student(student_id)

        # 仅更新有值的字段
        update_data = form.model_dump(exclude_unset=True)
        if not update_data:
            return student  # 没有需要更新的字段

        # 业务校验: 若修改了学号, 要校验新学号没被占
        if "student_no" in update_data and update_data["student_no"] != student.student_no:
            if self.dao.get_by_student_no(update_data["student_no"]):
                raise BizException(code=400, message=f"学号 {update_data['student_no']} 已被占用")

        for key, value in update_data.items():
            setattr(student, key, value)
        return self.dao.update(student)

    # ---------- D: Delete ----------
    def delete_student(self, student_id: int) -> None:
        """删除学生"""
        if not self.dao.delete_by_id(student_id):
            raise BizException(code=404, message=f"学生(id={student_id})不存在")
