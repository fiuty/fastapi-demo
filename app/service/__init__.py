"""
service 层: 业务逻辑
对应 Java 中的 Service 接口 + ServiceImpl (@Service)
注意: 这里不使用 dataclass 风格的 Service, 而是把 DAO 注入到 __init__ 中,
      用法接近 Spring 的 @Service + @Autowired
"""
from app.service.student_service import StudentService
from app.service.teacher_service import TeacherService

__all__ = ["StudentService", "TeacherService"]
