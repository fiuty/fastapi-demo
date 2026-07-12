# AgentScope 智能体对话模块文档

基于 [AgentScope 2.0](https://github.com/agentscope-ai/agentscope) 框架，在 FastAPI 项目中集成的智能体流式对话能力。

## 目录

- [架构总览](#架构总览)
- [项目结构](#项目结构)
- [配置说明](#配置说明)
- [数据库设计](#数据库设计)
- [Service 层](#service-层)
  - [AgentService](#agentservice)
  - [AgentscopeService](#chatservice)
  - [ToolkitService](#toolkitservice)
  - [ConversationService](#conversationservice)
- [Controller 层](#controller-层)
- [API 接口](#api-接口)
- [SSE 事件类型](#sse-事件类型)
- [多会话隔离与状态持久化](#多会话隔离与状态持久化)
- [上下文压缩](#上下文压缩)
- [工具 / MCP / Skill](#工具--mcp--skill)
- [应用生命周期](#应用生命周期)
- [使用示例](#使用示例)

---

## 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                        FastAPI 应用                          │
│                                                              │
│  ┌──────────────────┐  ┌──────────────────────────────────┐  │
│  │ ConversationCtrl │  │ AgentscopeDemoCtrl (chat/stream) │  │
│  │ /api/conversations│  │ /api/agentscope/chat/stream      │  │
│  └────────┬─────────┘  └──────────────┬───────────────────┘  │
│           │                           │                       │
│  ┌────────▼─────────┐  ┌──────────────▼───────────────────┐  │
│  │ ConversationSvc  │  │           AgentscopeService             │  │
│  │  会话 CRUD       │  │  流式对话编排 (加载状态→对话→持久化) │  │
│  └────────┬─────────┘  └──────────────┬───────────────────┘  │
│           │                           │                       │
│           │              ┌────────────▼───────────────────┐   │
│           │              │          AgentService           │   │
│           │              │  全局 Agent 单例 + RedisStorage  │   │
│           │              │  Model/Agent/State 管理          │   │
│           │              └────────────┬───────────────────┘   │
│           │                           │                       │
│  ┌────────▼─────────┐     ┌───────────▼──────────┐           │
│  │   DAO (MySQL)    │     │   RedisStorage        │           │
│  │ t_conversation   │     │   AgentState 持久化    │           │
│  │ t_message        │     │   (key_ttl=1天)       │           │
│  └──────────────────┘     └──────────────────────┘           │
└──────────────────────────────────────────────────────────────┘
```

### 核心设计

1. **全局 Agent 单例**：整个应用只创建一个 `Agent` 实例，通过切换 `AgentState` 实现多会话隔离
2. **Redis 状态持久化**：使用 AgentScope 原生 `RedisStorage`，支持多实例部署共享状态
3. **DB 消息持久化**：所有对话消息存入 MySQL，作为 Redis 过期后的 fallback 数据源
4. **SSE 流式输出**：前端通过 Server-Sent Events 实时接收 Agent 事件

---

## 项目结构

```
app/
├── config.py                              # 应用配置 (LLM/Redis/上下文压缩)
├── main.py                                # FastAPI 入口 (lifespan 初始化 RedisStorage)
├── model/
│   ├── conversation.py                    # 会话表 ORM (t_conversation, UUID主键)
│   └── message.py                         # 消息表 ORM (t_message, UUID主键)
├── pojo/
│   ├── conversation.py                    # 会话 Schema (ConversationCreate/Update/VO/DetailVO)
│   └── message.py                         # 消息 Schema (MessageVO, ChatRequest)
├── dao/
│   ├── conversation_dao.py                # 会话 DAO
│   └── message_dao.py                     # 消息 DAO
├── service/
│   ├── conversation_service.py            # 会话 CRUD 业务层
│   └── agentscope/
│       ├── __init__.py                    # 导出 AgentService, AgentscopeService, ToolkitService
│       ├── agent_service.py               # Agent/Model/State/Redis 管理
│       ├── chat_service.py                # 流式对话编排 + 事件映射
│       └── tool_kit_service.py            # Tool/MCP/Skill 管理服务
├── controller/
│   ├── conversation_controller.py         # 会话 CRUD 接口 (/api/conversations)
│   └── agentscope/
│       └── agentscope_demo_controller.py  # 流式对话接口 (/api/agentscope/chat/stream)
└── ...
```

---

## 配置说明

### .env 文件

```ini
# LLM 配置
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_NAME=deepseek-chat

# Redis 配置 (AgentState 持久化)
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
STATE_KEY_TTL=86400                   # 会话状态过期时间(秒), 1天

# 上下文压缩配置
CONTEXT_TRIGGER_RATIO=0.8             # token 超过模型上下文 80% 时触发压缩
CONTEXT_RESERVE_RATIO=0.1             # 压缩时保留最近 10% 的消息
```

### config.py 对应字段

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `LLM_API_KEY` | str | `""` | LLM API Key |
| `LLM_BASE_URL` | str | `https://api.deepseek.com` | LLM API 地址 |
| `LLM_MODEL_NAME` | str | `deepseek-v4-flash` | 模型名称 |
| `REDIS_HOST` | str | `127.0.0.1` | Redis 地址 |
| `REDIS_PORT` | int | `6379` | Redis 端口 |
| `REDIS_DB` | int | `0` | Redis 数据库 |
| `REDIS_PASSWORD` | str | `""` | Redis 密码 |
| `STATE_KEY_TTL` | int | `86400` | 状态过期时间(秒) |
| `CONTEXT_TRIGGER_RATIO` | float | `0.8` | 上下文压缩触发比例 |
| `CONTEXT_RESERVE_RATIO` | float | `0.1` | 压缩保留比例 |

### 依赖 (requirements.txt)

```
agentscope==2.0.2
apscheduler>=3.10.0
redis>=5.0,<6.0
```

> **注意**: `redis` 必须使用 5.x 版本 (兼容 Redis 3.x 服务端)。redis-py 8.x 要求 Redis 6+ 的 `HELLO` 命令支持。

---

## 数据库设计

### t_conversation (会话表)

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(36) PK | UUID 主键，由 Python `uuid.uuid4()` 生成 |
| `user_id` | VARCHAR(50) | 用户ID (当前写死 `default_user`) |
| `title` | VARCHAR(200) | 会话标题 |
| `create_time` | DATETIME | 创建时间 |
| `update_time` | DATETIME | 更新时间 (自动维护) |

### t_message (消息表)

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(36) PK | UUID 主键 |
| `conversation_id` | VARCHAR(36) FK | 外键 → `t_conversation.id` (CASCADE 删除) |
| `role` | VARCHAR(20) | 角色: `user` / `assistant` |
| `content` | TEXT | 消息内容 |
| `event_type` | VARCHAR(50) | 事件类型 (可空) |
| `extra_meta` | TEXT | 附加元数据 JSON (可空) |
| `create_time` | DATETIME | 创建时间 |

> 两表主键均为 UUID 字符串，用户传入的 `conversation_id` 直接作为主键查找，无需自增ID映射。

---

## Service 层

### AgentService

> `app/service/agentscope/agent_service.py`

Agent 工厂 + 全局 Agent 管理，核心职责：

1. **Model 创建**：封装 OpenAI / DashScope 模型创建
2. **Agent 创建**：组装 model + toolkit + context_config
3. **全局 Agent 单例**：整个应用共享一个 Agent 实例
4. **RedisStorage 管理**：初始化/关闭/读写 AgentState

#### Model 创建方法

| 方法 | 说明 |
|---|---|
| `create_model(api_key, base_url, model_name, ...)` | 创建默认 OpenAIChatModel (从 config 读取) |
| `create_openai_model(api_key, model_name, base_url, ...)` | 创建 OpenAI 兼容模型 |
| `create_dashscope_model(api_key, model_name, ...)` | 创建阿里云 DashScope 模型 |

#### Agent 创建方法

| 方法 | 说明 |
|---|---|
| `create_agent(name, system_prompt, model, tools, context_config, ...)` | 核心方法，需传入预创建的 model |
| `create_agent_with_default_model(name, system_prompt, ...)` | 便捷方法，自动从 config 创建 model |
| `_create_default_agent()` | 创建默认全局 Agent (name=assistant) |

#### 全局 Agent & State 管理

| 方法 | 类型 | 说明 |
|---|---|---|
| `init_storage()` | async | 初始化 RedisStorage (app 启动调用) |
| `close_storage()` | async | 关闭 RedisStorage (app 关闭调用) |
| `get_agent()` | sync | 获取全局 Agent 单例 |
| `set_agent_state(state)` | sync | 将 state 挂载到全局 Agent |
| `load_state(session_id)` | async | 从 Redis 加载会话状态 (None=过期/不存在) |
| `save_state(session_id, state)` | async | 写回 Redis (刷新滑动 TTL) |
| `create_state(session_id, state)` | async | 在 Redis 中创建新会话状态 |
| `remove_state(session_id)` | async | 删除 Redis 中的会话状态 |

#### 常量

```python
USER_ID = "default_user"          # 固定用户ID (后续接入用户体系时修改)
AGENT_ID = "global_assistant"     # 固定AgentID (Redis session key 组成部分)
```

---

### AgentscopeService

> `app/service/agentscope/chat_service.py`

流式对话编排服务，整合会话管理、消息持久化、Agent 流式调用。

#### chat_stream 流程

```
1. _resolve_conversation(conversation_id)
   → 查找/创建会话记录 (DB)

2. _save_message(role="user", content=user_message)
   → 用户消息持久化到 DB

3. AgentService.load_state(session_id)
   → 从 Redis 加载 AgentState
   → 命中: 直接使用 Redis 中的 state
   → 未命中: 从 DB 加载历史消息 → agent.observe(history) 重建 state

4. AgentService.set_agent_state(state)
   → 将 state 挂载到全局 Agent

5. agent.reply_stream(user_msg)
   → Agent 流式回复, 逐事件 yield SSE

6. finally: AgentService.save_state(session_id, agent.state)
   → 无论成功/异常, 都写回 Redis (刷新 TTL)

7. _save_message(role="assistant", content=full_response)
   → 助手回复持久化到 DB
```

#### 事件映射 (_map_event)

将 AgentScope 原生事件映射为前端可消费的 SSE 事件字典：

| AgentScope 事件 | SSE event | 关键 data 字段 |
|---|---|---|
| `ReplyStartEvent` | `reply_start` | `reply_id` |
| `ReplyEndEvent` | `reply_end` | `reply_id` |
| `TextBlockStartEvent` | `text_start` | `block_id` |
| `TextBlockDeltaEvent` | `text_delta` | `content`, `block_id` |
| `TextBlockEndEvent` | `text_end` | `block_id` |
| `ThinkingBlockStartEvent` | `thinking_start` | `block_id` |
| `ThinkingBlockDeltaEvent` | `thinking_delta` | `content`, `block_id` |
| `ThinkingBlockEndEvent` | `thinking_end` | `block_id` |
| `ToolCallStartEvent` | `tool_call_start` | `tool_call_id`, `tool_call_name` |
| `ToolCallDeltaEvent` | `tool_call_delta` | `tool_call_id`, `delta` |
| `ToolCallEndEvent` | `tool_call_end` | `tool_call_id` |
| `ToolResultStartEvent` | `tool_result_start` | `tool_call_id`, `tool_call_name` |
| `ToolResultTextDeltaEvent` | `tool_result_delta` | `tool_call_id`, `content` |
| `ToolResultEndEvent` | `tool_result_end` | `tool_call_id`, `state` |
| `ModelCallStartEvent` | `model_call_start` | `model_name` |
| `ModelCallEndEvent` | `model_call_end` | `input_tokens`, `output_tokens` |
| `HintBlockEvent` | `hint` | `block_id`, `hint`, `source` |
| `ExceedMaxItersEvent` | `error` | `message` |

---

### ToolkitService

> `app/service/agentscope/tool_kit_service.py`

统一管理 Agent 的三种扩展能力：

| 能力 | 方法 | 说明 |
|---|---|---|
| **Tool** | `create_tool(func, ...)` | Python 函数 → FunctionTool (需 docstring + 类型注解) |
| **Tool** | `create_tools(funcs)` | 批量创建函数工具 |
| **MCP (Stdio)** | `create_stdio_mcp(name, command, args, ...)` | 子进程通信的 MCP 客户端 |
| **MCP (HTTP)** | `create_http_mcp(name, url, headers, ...)` | HTTP/SSE 通信的 MCP 客户端 |
| **Skill** | `create_skill_loader(directory, scan_subdir)` | 本地 Skill 目录加载器 |
| **组装** | `create_toolkit(tools, mcps, skills_or_loaders)` | 合并为 Toolkit 传给 Agent |
| **空工具** | `create_empty_toolkit()` | 无工具的空 Toolkit |

三种能力的区别：

- **Tool**: 自定义 Python 函数，Agent 直接调用执行
- **MCP**: 标准化外部工具协议，连接外部 MCP Server (如 filesystem、web-search)
- **Skill**: 指令+脚本+资源的集合，Agent 通过 `skill_viewer` 工具读取指令后按需执行

---

### ConversationService

> `app/service/conversation_service.py`

会话 CRUD 业务层，标准 Controller → Service → DAO 分层：

| 方法 | 说明 |
|---|---|
| `create_conversation(form)` | 创建会话 |
| `get_conversation(conversation_id)` | 查询会话 (不存在抛 BizException) |
| `get_conversation_detail(conversation_id)` | 查询会话详情 (含消息列表) |
| `page_conversations(page, size)` | 分页查询会话列表 |
| `update_conversation(conversation_id, form)` | 更新会话标题 |
| `delete_conversation(conversation_id)` | 删除会话 (async, 同时清理 Redis 状态) |

---

## Controller 层

### conversation_controller.py

> 路由前缀: `/api/conversations`，标签: `会话管理`

标准 CRUD RESTful 接口。

### agentscope_demo_controller.py

> 路由前缀: `/api/agentscope`，标签: `AgentScope 对话`

仅包含流式对话接口，通过 `StreamingResponse` 返回 SSE 流。

---

## API 接口

### 会话管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/conversations` | 创建会话 |
| `GET` | `/api/conversations` | 分页查询会话列表 |
| `GET` | `/api/conversations/{conversation_id}` | 查询会话详情 (含消息) |
| `PUT` | `/api/conversations/{conversation_id}` | 更新会话标题 |
| `DELETE` | `/api/conversations/{conversation_id}` | 删除会话 (级联删除消息 + 清理 Redis) |

### 流式对话

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/agentscope/chat/stream` | SSE 流式对话 |

**请求体** (`ChatRequest`)：

```json
{
  "conversation_id": "9f4fe10c-4669-45f7-b950-5b62994c4a15",
  "message": "你好"
}
```

- `conversation_id`：可选，不传则创建新会话。首次对话后从 SSE `conversation_id` 事件获取，后续对话传回该值。
- `message`：必填，用户消息内容。

**响应**：`text/event-stream` (SSE)

---

## SSE 事件类型

前端通过 `EventSource` 或 `fetch` 读取 SSE 流，根据 `event` 字段渲染不同 UI：

| event | data 示例 | 前端处理 |
|---|---|---|
| `conversation_id` | `{"conversation_id": "xxx"}` | 保存会话ID，后续对话传回 |
| `reply_start` | `{"reply_id": "xxx"}` | 对话开始标记 |
| `text_start` | `{"block_id": "xxx"}` | 文本块开始 |
| `text_delta` | `{"content": "你", "block_id": "xxx"}` | **文本增量，拼接渲染** |
| `text_end` | `{"block_id": "xxx"}` | 文本块结束 |
| `thinking_start` | `{"block_id": "xxx"}` | 思维链开始 (折叠显示) |
| `thinking_delta` | `{"content": "...", "block_id": "xxx"}` | 思维链增量 |
| `thinking_end` | `{"block_id": "xxx"}` | 思维链结束 |
| `tool_call_start` | `{"tool_call_id": "x", "tool_call_name": "get_weather"}` | 工具调用开始 |
| `tool_call_delta` | `{"tool_call_id": "x", "delta": "..."}` | 工具调用参数增量 |
| `tool_call_end` | `{"tool_call_id": "x"}` | 工具调用结束 |
| `tool_result_start` | `{"tool_call_id": "x", "tool_call_name": "..."}` | 工具结果开始 |
| `tool_result_delta` | `{"tool_call_id": "x", "content": "..."}` | 工具结果增量 |
| `tool_result_end` | `{"tool_call_id": "x", "state": "..."}` | 工具结果结束 |
| `model_call_start` | `{"model_name": "deepseek-chat"}` | 模型调用开始 |
| `model_call_end` | `{"input_tokens": 100, "output_tokens": 50}` | 模型调用结束 (含 token 统计) |
| `reply_end` | `{"reply_id": "xxx"}` | 对话结束标记 |
| `assistant_message_id` | `{"message_id": "xxx"}` | 助手消息的DB ID |
| `error` | `{"message": "错误信息"}` | 异常事件 |
| `done` | `{}` | 流结束 |

### SSE 数据格式

每条 SSE 消息格式：

```
event: text_delta
data: {"content": "你好", "block_id": "abc123"}

```

---

## 多会话隔离与状态持久化

### 问题背景

多实例部署 (A/B 实例) 时，Agent 状态存放在进程内存中，请求路由到不同实例会丢失上下文。

### 解决方案

使用 AgentScope 原生 `RedisStorage` 持久化 `AgentState`：

```
对话流程:
  请求 → load_state(Redis) → 命中?
    → 是: 用 Redis 中的 state (毫秒级)
    → 否: 从 DB t_message 恢复历史 → 重建 state
  → set_agent_state → reply_stream
  → finally: save_state(Redis, 刷新 TTL)
```

### Redis Key 结构

RedisStorage 使用以下 key 模板：

```
agentscope:user:{user_id}:session:{session_id}         → SessionRecord (含 AgentState)
agentscope:user:{user_id}:agent:{agent_id}:sessions    → Set (session_id 索引)
```

当前固定值：

- `user_id` = `default_user`
- `agent_id` = `global_assistant`
- `session_id` = `conversation.id` (UUID)

### TTL 过期机制

- `key_ttl=86400` (1天)：每次 `save_state` 写入时刷新 TTL (滑动窗口)
- 活跃会话持续续期，不活跃会话 1 天后自动过期
- 过期后下次对话从 DB 恢复历史消息重建 state

### 内存控制

| 层级 | 机制 | 说明 |
|---|---|---|
| 框架层 | `ContextConfig` 自动压缩 | token 达到 80% 时 LLM 总结旧消息为 summary |
| Redis 层 | `key_ttl` 滑动过期 | 不活跃会话自动释放 |
| Redis 层 | `maxmemory` + LRU (建议) | Redis 服务端兜底防护 |

---

## 上下文压缩

AgentScope 内置上下文压缩机制，在 `reply_stream` 的 reasoning 阶段自动触发。

### 配置 (config.py)

```python
CONTEXT_TRIGGER_RATIO = 0.8   # token 达到模型上下文窗口 80% 时触发
CONTEXT_RESERVE_RATIO = 0.1   # 压缩时保留最近 10% 的消息
```

### 压缩流程

1. Agent 在 reasoning 前检查 `state.context` 的 token 数
2. 超过 `trigger_ratio * model.context_size` (如 0.8 * 128K = 102K tokens) 时触发
3. 旧消息由 LLM 总结为结构化 `summary` (task_overview / current_state / next_steps 等)
4. `state.context` 替换为 `[summary + 最近 reserve_ratio 的消息]`
5. 压缩后的 state 写回 Redis，后续对话基于压缩后上下文

### 效果

- 单个 AgentState 体积可控 (压缩后约 2-5KB)
- 10 万活跃会话约 500MB Redis 内存
- 对话历史不丢失，旧信息以 summary 形式保留

---

## 工具 / MCP / Skill

通过 `ToolkitService` 创建工具能力，组装为 `Toolkit` 后传给 `Agent`。

### 自定义函数工具 (Tool)

```python
from app.service.agentscope import ToolkitService

def get_weather(city: str) -> str:
    '''查询天气

    Args:
        city: 城市名称
    '''
    return f"{city}今天晴"

weather_tool = ToolkitService.create_tool(get_weather)
```

### MCP 工具

```python
# Stdio MCP (子进程)
mcp = ToolkitService.create_stdio_mcp(
    name="filesystem",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
)

# HTTP MCP (远程)
mcp = ToolkitService.create_http_mcp(
    name="web-search",
    url="http://localhost:8080/mcp",
    headers={"Authorization": "Bearer xxx"},
)
```

### Skill

```python
loader = ToolkitService.create_skill_loader("./skills")
```

### 组装并传入 Agent

```python
toolkit = ToolkitService.create_toolkit(
    tools=[weather_tool],
    mcps=[mcp],
    skills_or_loaders=[loader],
)

agent = AgentService.create_agent(
    name="助手",
    system_prompt="You are a helpful assistant.",
    model=model,
    toolkit=toolkit,
)
```

---

## 应用生命周期

`main.py` 使用 FastAPI `lifespan` 管理启动/关闭：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动
    init_db()                              # 初始化数据库表
    await AgentService.init_storage()      # 初始化 RedisStorage (创建连接池)
    yield
    # 关闭
    await AgentService.close_storage()     # 关闭 RedisStorage (释放连接池)
```

---

## 使用示例

### 1. 完整对话流程 (curl)

```bash
# 第一次对话 (不传 conversation_id, 自动创建)
curl -X POST http://127.0.0.1:8000/api/agentscope/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "你好，我叫张三"}'

# SSE 响应中获取 conversation_id
# event: conversation_id
# data: {"conversation_id": "9f4fe10c-4669-45f7-b950-5b62994c4a15"}

# 第二次对话 (传回 conversation_id, 带上下文)
curl -X POST http://127.0.0.1:8000/api/agentscope/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"conversation_id": "9f4fe10c-4669-45f7-b950-5b62994c4a15", "message": "我叫什么名字？"}'

# Agent 应能回答 "张三" (上下文连续)
```

### 2. 会话管理

```bash
# 查询会话详情 (含所有消息)
curl http://127.0.0.1:8000/api/conversations/9f4fe10c-4669-45f7-b950-5b62994c4a15

# 分页查询会话列表
curl "http://127.0.0.1:8000/api/conversations?page=1&size=10"

# 删除会话 (同时清理 DB + Redis)
curl -X DELETE http://127.0.0.1:8000/api/conversations/9f4fe10c-4669-45f7-b950-5b62994c4a15
```

### 3. 带工具的 Agent

```python
from app.service.agentscope import AgentService, ToolkitService

# 定义工具函数
def calculate(expression: str) -> str:
    '''计算数学表达式

    Args:
        expression: 数学表达式, 如 "1+2*3"
    '''
    try:
        return str(eval(expression))
    except Exception as e:
        return f"计算失败: {e}"

# 组装 Toolkit
toolkit = ToolkitService.create_toolkit(tools=[
    ToolkitService.create_tool(calculate),
])

# 创建带工具的 Agent
model = AgentService.create_model()
agent = AgentService.create_agent(
    name="助手",
    system_prompt="You are a helpful assistant.",
    model=model,
    toolkit=toolkit,
)
```
