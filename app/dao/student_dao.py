"""
学生 DAO
对应 Java:
    public interface StudentMapper extends BaseMapper<Student> {
        ...
    }
"""
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.pojo.student import Student


class StudentDAO:
    """学生数据访问对象, 每个方法对应一个 SQL 操作"""

    def __init__(self, db: Session):
        self.db = db

    # ---------- 单条查询 ----------
    def get_by_id(self, student_id: int) -> Optional[Student]:
        return self.db.get(Student, student_id)

    def get_by_student_no(self, student_no: str) -> Optional[Student]:
        stmt = select(Student).where(Student.student_no == student_no)
        return self.db.execute(stmt).scalar_one_or_none()

    # ---------- 分页/条件查询 ----------
    def page_query(
        self,
        name: Optional[str] = None,
        student_no: Optional[str] = None,
        clazz: Optional[str] = None,
        page: int = 1,
        size: int = 10,
    ) -> tuple[list[Student], int]:
        """
        条件分页查询
        返回 (records, total)
        等价于 MyBatis-Plus:
            IPage<Student> page = new Page<>(page, size);
            LambdaQueryWrapper<Student> wrapper = new LambdaQueryWrapper<>();
            wrapper.like(name != null, Student::getName, name)
                   .eq(studentNo != null, Student::getStudentNo, studentNo)
                   .eq(clazz != null, Student::getClazz, clazz);
            studentMapper.selectPage(page, wrapper);
        """
        conditions = []
        if name:
            conditions.append(Student.name.like(f"%{name}%"))
        if student_no:
            conditions.append(Student.student_no == student_no)
        if clazz:
            conditions.append(Student.clazz == clazz)

        # 列表查询
        list_stmt = select(Student).where(*conditions).order_by(Student.id.desc())
        list_stmt = list_stmt.offset((page - 1) * size).limit(size)
        records = list(self.db.execute(list_stmt).scalars().all())

        # 计数查询
        count_stmt = select(func.count(Student.id)).where(*conditions)
        total = self.db.execute(count_stmt).scalar_one()

        return records, total

    # ---------- 新增 ----------
    def insert(self, student: Student) -> Student:
        self.db.add(student)
        self.db.flush()  # 刷出以拿到自增 ID, 但不提交事务
        self.db.refresh(student)
        return student

    # ---------- 更新 ----------
    def update(self, student: Student) -> Student:
        self.db.flush()
        self.db.refresh(student)
        return student

    # ---------- 删除 ----------
    def delete_by_id(self, student_id: int) -> bool:
        student = self.db.get(Student, student_id)
        if student is None:
            return False
        self.db.delete(student)
        self.db.flush()
        return True
