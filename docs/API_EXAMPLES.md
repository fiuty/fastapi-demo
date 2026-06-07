# API 调试示例

> 用 `curl` / Python 一行命令验证接口；所有示例均已实际跑通。

## 1. 启动服务

```bash
# 终端 A: 启动 (开发模式, 自动 reload)
cd F:\projects\python\2026\framework\fastapi
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

```bash
# 终端 B: 看到下面这行表示启动成功
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

## 2. 浏览器

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc:      <http://127.0.0.1:8000/redoc>
- 健康检查:    <http://127.0.0.1:8000/>

## 3. curl 调试

> Windows 上推荐装 Git Bash，或用 `curl.exe`（PowerShell 自带）。

### 学生接口

```bash
# 1) 新增学生
curl -X POST http://127.0.0.1:8000/api/students \
     -H "Content-Type: application/json; charset=utf-8" \
     -d '{"name":"张三","student_no":"S001","age":20,"gender":"男","clazz":"高三(1)班","email":"zhangsan@example.com"}'

# 2) 按 ID 查询
curl http://127.0.0.1:8000/api/students/1

# 3) 分页查询 (条件 + 分页)
curl "http://127.0.0.1:8000/api/students?name=张&page=1&size=10"

# 4) 局部更新
curl -X PUT http://127.0.0.1:8000/api/students/1 \
     -H "Content-Type: application/json; charset=utf-8" \
     -d '{"age":21,"email":"new@example.com"}'

# 5) 删除
curl -X DELETE http://127.0.0.1:8000/api/students/1
```

### 教师接口

```bash
# 1) 新增教师
curl -X POST http://127.0.0.1:8000/api/teachers \
     -H "Content-Type: application/json; charset=utf-8" \
     -d '{"name":"王老师","teacher_no":"T001","age":35,"gender":"女","subject":"数学","salary":8000}'

# 2) 按学科精确过滤
curl "http://127.0.0.1:8000/api/teachers?subject=数学&page=1&size=10"
```

## 4. PowerShell 调试

> **注意**：PowerShell 5.1 默认 GBK 编码，中文会乱码；建议改用 `Invoke-RestMethod` + UTF-8 字节流。

```powershell
# 1) 编码 UTF-8 后发送
$body = '{"name":"张三","student_no":"S001","age":20,"gender":"男","clazz":"高三(1)班"}'
$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8000/api/students" `
                  -ContentType "application/json; charset=utf-8" -Body $bytes

# 2) 查询
Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8000/api/students?page=1&size=10"

# 3) 异常测试 (PS 5.1 没有 -StatusCodeVariable, 用 try/catch)
try {
    Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8000/api/students/999" -ErrorAction Stop
} catch {
    $_.Exception.Response.StatusCode.value__   # HTTP 状态码
    # 读取响应体
    $reader = [System.IO.StreamReader]::new($_.Exception.Response.GetResponseStream())
    $reader.ReadToEnd()
}
```

## 5. Python 脚本 (推荐用于复杂测试)

```python
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

def call(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    headers = {"Content-Type": "application/json; charset=utf-8"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


# 新增
print(call("POST", "/api/students", {
    "name": "张三", "student_no": "S001",
    "age": 20, "gender": "男", "clazz": "高三(1)班"
}))

# 查询
print(call("GET", "/api/students/1"))

# 分页
print(call("GET", "/api/students?page=1&size=10"))

# 更新
print(call("PUT", "/api/students/1", {"age": 21}))

# 删除
print(call("DELETE", "/api/students/1"))

# 异常: 不存在
print(call("GET", "/api/students/999"))
# -> (200, {"code": 404, "message": "学生(id=999)不存在", "data": None})

# 异常: 学号重复
print(call("POST", "/api/students", {"name":"x", "student_no":"S001", "age":1, "clazz":"y"}))
# -> (200, {"code": 400, "message": "学号 S001 已存在", "data": None})

# 异常: 参数错误 (age=999)
print(call("POST", "/api/students", {"name":"x", "student_no":"S999", "age":999, "clazz":"y"}))
# -> (422, {"code": 400, "message": "body.age: Input should be less than or equal to 200", ...})
```

## 6. 响应格式规范

### 成功
```json
{
  "code": 200,
  "message": "success",
  "data": { ... }
}
```

### 业务失败 (HTTP 200, code 非 200)
```json
{
  "code": 400,
  "message": "学号 S001 已存在",
  "data": null
}
```

### 参数校验失败 (HTTP 422)
```json
{
  "code": 400,
  "message": "body.age: Input should be less than or equal to 200",
  "data": [
    {
      "type": "less_than_equal",
      "loc": ["body", "age"],
      "msg": "Input should be less than or equal to 200",
      "input": 999,
      "ctx": { "le": 200 }
    }
  ]
}
```

### HTTP 错误 (404/405/500)
```json
{
  "code": 404,
  "message": "Not Found",
  "data": null
}
```

## 7. 在 Swagger UI 里调试 (推荐)

1. 打开 <http://127.0.0.1:8000/docs>
2. 找到 "学生管理" 标签下的 `POST /api/students`
3. 点 "Try it out" → 改 request body → "Execute"
4. 下方看 "Server response" 区
5. 改字段类型 / 必填校验直接看效果（422 + 详细错误信息）
