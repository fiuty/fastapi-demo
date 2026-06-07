"""
教师 DAO
"""
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.model.teacher import Teacher


class TeacherDAO:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, teacher_id: int) -> Optional[Teacher]:
        return self.db.get(Teacher, teacher_id)

    def get_by_teacher_no(self, teacher_no: str) -> Optional[Teacher]:
        stmt = select(Teacher).where(Teacher.teacher_no == teacher_no)
        return self.db.execute(stmt).scalar_one_or_none()

    def page_query(
        self,
        name: Optional[str] = None,
        teacher_no: Optional[str] = None,
        subject: Optional[str] = None,
        page: int = 1,
        size: int = 10,
    ) -> tuple[list[Teacher], int]:
        conditions = []
        if name:
            conditions.append(Teacher.name.like(f"%{name}%"))
        if teacher_no:
            conditions.append(Teacher.teacher_no == teacher_no)
        if subject:
            conditions.append(Teacher.subject == subject)

        list_stmt = (
            select(Teacher)
            .where(*conditions)
            .order_by(Teacher.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        records = list(self.db.execute(list_stmt).scalars().all())

        count_stmt = select(func.count(Teacher.id)).where(*conditions)
        total = self.db.execute(count_stmt).scalar_one()

        return records, total

    def insert(self, teacher: Teacher) -> Teacher:
        self.db.add(teacher)
        self.db.flush()
        self.db.refresh(teacher)
        return teacher

    def update(self, teacher: Teacher) -> Teacher:
        self.db.flush()
        self.db.refresh(teacher)
        return teacher

    def delete_by_id(self, teacher_id: int) -> bool:
        teacher = self.db.get(Teacher, teacher_id)
        if teacher is None:
            return False
        self.db.delete(teacher)
        self.db.flush()
        return True
