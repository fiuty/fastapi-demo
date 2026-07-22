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
- [模型重试与回退通知](#模型重试与回退通知)
- [多会话隔离与状态持久化](#多会话隔离与状态持久化)
- [智能体中断与恢复](#智能体中断与恢复)
- [多实例中断协调](#多实例中断协调)
- [上下文压缩](#上下文压缩)
- [工具 / MCP / Skill](#工具--mcp--skill)
- [应用生命周期](#应用生命周期)
- [使用示例](#使用示例)

---

## 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                        FastAPI 应用                               │
│                                                                   │
│  ┌──────────────────┐  ┌──────────────────────────────────┐      │
│  │ ConversationCtrl │  │ AgentscopeDemoCtrl (chat/stream) │      │
│  │ /api/conversations│  │ /api/agentscope/chat/stream      │      │
│  │                   │  │ /api/agentscope/chat/interrupt   │      │
│  └────────┬─────────┘  └──────────────┬───────────────────┘      │
│           │                           │                           │
│  ┌────────▼─────────┐  ┌──────────────▼───────────────────────┐  │
│  │ ConversationSvc  │  │         AgentscopeService             │  │
│  │  会话 CRUD       │  │  流式对话编排 + 中断/恢复逻辑          │  │
│  │                   │  │  _running_tasks + _interrupt_events   │  │
│  └────────┬─────────┘  └──────────────┬───────────────────────┘  │
│           │                           │                           │
│           │              ┌────────────▼───────────────────────┐   │
│           │              │          AgentService               │   │
│           │              │  全局 Agent 单例 + RedisStorage      │   │
│           │              │  Model/Agent/State 管理              │   │
│           │              └────────────┬───────────────────────┘   │
│           │                           │                           │
│           │              ┌────────────▼───────────────────────┐   │
│           │              │     InterruptCoordinator (单例)      │   │
│           │              │  Redis Pub/Sub 跨实例中断协调        │   │
│           │              │  后台监听 + 请求广播 + 响应回传       │   │
│           │              └────────────┬───────────────────────┘   │
│           │                           │                           │
│  ┌────────▼─────────┐     ┌───────────▼──────────────┐           │
│  │   DAO (MySQL)    │     │        Redis              │           │
│  │ t_conversation   │     │  AgentState 持久化         │           │
│  │ t_message        │     │  interrupt:task:{id}       │           │
│  └──────────────────┘     │  Pub/Sub 中断频道          │           │
│                            └──────────────────────────┘           │
└──────────────────────────────────────────────────────────────────┘
```

### 核心设计

1. **全局 Agent 单例**：整个应用只创建一个 `Agent` 模板实例，通过 `create_agent_with_state()` 创建独立 Agent 绑定各自的 `AgentState`，实现多会话隔离
2. **Redis 状态持久化**：使用 AgentScope 原生 `RedisStorage`，支持多实例部署共享会话状态
3. **DB 消息持久化**：所有对话消息存入 MySQL，作为 Redis 过期后的 fallback 数据源
4. **SSE 流式输出**：前端通过 Server-Sent Events 实时接收 Agent 事件
5. **跨实例中断协调**：基于 Redis Pub/Sub 实现多实例部署下的 `asyncio.Task` 跨进程取消，详见 [多实例中断协调](#多实例中断协调)

---

## 项目结构

```
app/
├── config.py                              # 应用配置 (LLM/Redis/上下文压缩)
├── main.py                                # FastAPI 入口 (lifespan 初始化 RedisStorage + InterruptCoordinator)
├── middleware/
│   ├── __init__.py                        # 导出 ModelRetryNotifierMiddleware
│   └── model_retry_notifier.py            # 模型重试/回退通知中间件
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
│       ├── agentscope_service.py          # 流式对话编排 + 事件映射 + 中断逻辑
│       ├── interrupt_coordinator.py       # Redis Pub/Sub 跨实例中断协调器
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
LLM_MODEL_NAME=deepseek-v4-flash

# 备用 LLM 配置 (主模型不可用时自动回退)
BACK_LLM_API_KEY=sk-yyy
BACK_LLM_BASE_URL=https://api.deepseek.com
BACK_LLM_MODEL_NAME=deepseek-v4-pro

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
| `BACK_LLM_API_KEY` | str | `""` | 备用模型 API Key |
| `BACK_LLM_BASE_URL` | str | `https://api.deepseek.com` | 备用模型 API 地址 |
| `BACK_LLM_MODEL_NAME` | str | `deepseek-v4-pro` | 备用模型名称 |
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
| `create_agent(name, system_prompt, model, tools, max_retries, ...)` | 核心方法，需传入预创建的 model。默认注入 `ModelRetryNotifierMiddleware` 和 `fallback_model` |
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

> `app/service/agentscope/agentscope_service.py`

流式对话编排服务，整合会话管理、消息持久化、Agent 流式调用、中断与恢复。

#### 核心方法

| 方法 | 说明 |
|---|---|
| `chat_stream(user_message, conversation_id)` | SSE 流式对话，支持新消息和恢复中断 |
| `chat_stream_interactive(...)` | 交互式流式对话，含工具权限确认 |
| `interrupt(conversation_id)` | 中断对话 (本地/远程运行中 + 暂停中) |
| `_interruptible_agent_stream(...)` | 中断感知的 Agent 事件流包装器 |
| `_cancel_and_wait_task(...)` | 取消本地 asyncio Task 并等待清理 |
| `_interrupt_parked_agent(...)` | 中断暂停中的智能体 (UserInterruptEvent) |

#### 任务注册与中断协调

```python
# 对话开始时注册任务 (本地 + Redis)
AgentscopeService._register_task(session_id, current_task)
  ├─ _running_tasks[session_id] = task            # 本地内存
  └─ InterruptCoordinator.register_task(id)        # Redis (跨实例可见)

# 对话结束时注销任务
AgentscopeService._unregister_task(session_id)
  ├─ _running_tasks.pop(session_id)                # 本地内存
  └─ InterruptCoordinator.deregister_task(id)      # Redis
```

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
| `POST` | `/api/agentscope/chat/interrupt` | 中断对话 (运行中或暂停中) |
| `POST` | `/api/agentscope/chat/stream/interactive` | 交互式流式对话 (含工具权限确认) |

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
| `model_retry` | `{"type":"retry","model_name":"deepseek-v4-flash","attempt":1,"is_fallback":false,"error":"...","message":"..."}` | 模型重试/回退通知 |
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

## 模型重试与回退通知

### 背景

由于主模型可能不稳定（超时、503 等），Agent 在创建时通过 `ModelConfig` 配置了 `max_retries=3`（最大重试次数）和 `fallback_model`（备用模型）。AgentScope 的 `_call_model` 内部按以下策略运行：

```
models = [主模型, 备用模型]
for model in models:
    for attempt in range(max_retries + 1):   # 共 4 次尝试
        try: await model(...); return
        except: log "Retrying (attempt/max_retries)"
```

重试/回退期间，Agent 事件流被阻塞（`await _call_model()` 不 yield 任何事件），前端在这段时间内无任何反馈，用户体验较差。

### 解决方案

利用 AgentScope 2.0.4 的 **`on_model_call` 洋葱钩子（middleware hook）**，在每次模型调用失败时捕获异常信息，通过 `asyncio.Queue` 实时推送。`_interruptible_agent_stream` 通过 `asyncio.wait` 同时监听三路信号（Agent 事件 / 中断 / retry_queue），重试通知到达时**立即** yield，不等待 Agent 生成器恢复。

### 架构

```
chat_stream()
  │
  ├─ retry_queue = asyncio.Queue()
  ├─ agent.state.middle_context["_retry_queue"] = retry_queue
  │
  └─ _interruptible_agent_stream(agent_gen, interrupt_event, retry_queue)
       │
       │  retry_queue is None → 原有二路竞争 (agent_gen vs interrupt) 不受影响
       │
        │  retry_queue is not None → 生产者-消费者模式:
        │
        ├── agent_producer (asyncio.Task)
        │    │  独占消费 agent_gen.__anext__()
        │    │  同时监听 interrupt_event 实现中断
        │    │  → event_queue.put(agent_event)
        │    │  → agent_done.set()  // 流结束
        │    │
        │    │  关键: __anext__() 仅被此 task 独占调用，
        │    │  永不与其他 task 竞争或中途 cancel，
        │    │  避免异步生成器的并发状态污染。
        │    │
        └── 主循环 (三路竞争, 无独立 retry_producer)
             │  asyncio.wait([
             │      event_queue.get(),    // Agent 事件
             │      retry_queue.get(),    // 重试通知 (直接读取, 不经中转)
             │      agent_done.wait(),    // 结束信号
             │  ])
             │  按优先级 yield:
             │    1. get_event 获胜  → yield AgentScope 事件
             │    2. get_retry 获胜  → yield RetryNotification (实时推送)
             │    3. wait_done 获胜  → agent_done 且 queue 空 → 退出
             │
             │  优化: 主循环直接读 retry_queue, 省去了原先
             │  retry_producer → event_queue 的中转环节，
             │  减少一个 asyncio.Task 和一次 Queue 间拷贝。
```

**为什么必须用生产者-消费者模式？**

`agent_gen.__anext__()` 在 `_call_model` 阻塞期间是一个**长生命周期**的 coroutine。如果在三路 `asyncio.wait` 竞争中，retry_queue 获胜后对 `__anext__()` 的 future 执行 `cancel()` 然后下一轮重新创建，会破坏异步生成器的内部状态（同一生成器不能有两个并发的 `__anext__()` 调用）。

将 Agent 事件消费作为独立后台 Task（`agent_producer`），通过中间 `event_queue` 与主循环解耦，`__anext__()` 只被 `agent_producer` 独占，**永不中途取消**，保证生成器状态安全。重试通知则由主循环直接从 `retry_queue` 读取——主循环的 `asyncio.wait` 同时竞争 `event_queue.get()`、`retry_queue.get()` 和 `agent_done.wait()` 三路信号，不再需要独立的 `retry_producer` 做中转。

`_call_model` 阻塞期间，`agent_producer` 的 `__anext__()` 卡在 `await _call_model()` 上。中间件通过 `retry_queue.put_nowait()` 写入通知 → 主循环的 `retry_queue.get()` 立即返回 → **实时 yield 给前端**。两条路径完全独立，互不阻塞。

### 新增文件

#### `app/middleware/model_retry_notifier.py`

`ModelRetryNotifierMiddleware` 继承 `MiddlewareBase`，实现 `on_model_call` hook。识别三种通知类型，通过 `put_nowait` 写入 `asyncio.Queue`（存储在 `agent.state.middle_context["_retry_queue"]`）：

| 通知 type | 触发条件 | 含义 |
|-----------|---------|------|
| `retry` | `next_handler()` 抛出异常 | 当前模型调用失败，AgentScope 将自动重试 |
| `fallback` | 首次见到新模型名且非首个模型 | 主模型不可用，切换到备用模型 |
| `recovery` | 某模型 attempts > 1 后 `next_handler()` 成功 | 重试后调用成功，模型恢复 |

通知通过 `put_nowait` 写入 `retry_queue`，`_interruptible_agent_stream` 的主循环通过 `asyncio.wait` 直接竞争 `retry_queue.get()`，通知到达时立即 yield 不等待 Agent 事件。

### 涉及改动的文件

| 文件 | 改动 |
|------|------|
| `app/middleware/__init__.py` | 导出 `ModelRetryNotifierMiddleware` |
| `app/middleware/model_retry_notifier.py` | **新增** 中间件实现, 使用平铺 `{model: count}` 追踪状态 |
| `app/service/agentscope/agent_service.py` | `create_agent()` / `create_agent_with_state()` 默认注入中间件；新增 `_middlewares` 类变量复用 |
| `app/service/agentscope/agentscope_service.py` | 新增 `RetryNotification` 类；`_interruptible_agent_stream` 采用生产者-消费者模式（`agent_producer` + 主循环三路竞争 `event_queue` / `retry_queue` / `agent_done`）；`chat_stream()` / `chat_stream_interactive()` 注入 retry_queue 并消费 `RetryNotification`；`_persist_after_stream` 清理 non-serializable 上下文 |

### SSE 事件示例

当主模型 `deepseek-v4-flash` 完全不可用，自动回退到备用模型时的完整事件流：

```
event: model_call_start
data: {"model_name":"deepseek-v4-flash"}

event: model_retry
data: {"type":"retry","model_name":"deepseek-v4-flash","attempt":1,"is_fallback":false,"error":"Error code: 503","message":"模型 deepseek-v4-flash 调用失败，正在重试 (1/3)..."}

event: model_retry
data: {"type":"retry","model_name":"deepseek-v4-flash","attempt":2,"is_fallback":false,"error":"Error code: 503","message":"模型 deepseek-v4-flash 调用失败，正在重试 (2/3)..."}

event: model_retry
data: {"type":"retry","model_name":"deepseek-v4-flash","attempt":3,"is_fallback":false,"error":"Error code: 503","message":"模型 deepseek-v4-flash 调用失败，正在重试 (3/3)..."}

event: model_retry
data: {"type":"fallback","model_name":"deepseek-v4-pro","is_fallback":true,"message":"主模型不可用，切换到备用模型: deepseek-v4-pro"}

event: model_retry
data: {"type":"recovery","model_name":"deepseek-v4-pro","message":"模型 deepseek-v4-pro 第1次调用成功"}

event: model_call_end
data: {"input_tokens":1234,"output_tokens":567}

event: text_block_start
data: {"block_id":"..."}

event: text_block_delta
data: {"content":"你好！请问有什么可以帮你的？","block_id":"..."}
...
```

### `model_retry` 数据字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | `"retry"` / `"fallback"` / `"recovery"` |
| `model_name` | string | 当前调用的模型名称 |
| `attempt` | int | 当前模型第几次尝试 (仅 `retry` 类型) |
| `is_fallback` | bool | 是否已切换到备用模型 (仅 `retry` 类型) |
| `error` | string | 异常信息前 300 字符 (仅 `retry` 类型) |
| `message` | string | 人类可读的提示消息，可直接展示 |

### 前端对接建议

```javascript
eventSource.addEventListener('model_retry', (e) => {
    const data = JSON.parse(e.data);
    switch (data.type) {
        case 'retry':
            showToast(`模型响应异常，正在重试 (${data.attempt}/3)...`, 'warning');
            break;
        case 'fallback':
            showToast(`正在切换到备用模型: ${data.model_name}`, 'info');
            break;
        case 'recovery':
            // 可选：隐藏 loading 状态，无需展示给用户
            hideLoading();
            break;
    }
});
```

> **注意**：`RetryNotification` 通过生产者-消费者模式实现**实时推送**。`agent_producer` 是独立的 `asyncio.Task`，主循环通过 `asyncio.wait` 直接竞争 `event_queue.get()`、`retry_queue.get()` 和 `agent_done.wait()` 三路信号。中间件每次 `put_nowait` 后，主循环的 `retry_queue.get()` 立即返回并 yield——与 `_call_model` 是否阻塞无关。

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

## 智能体中断与恢复

AgentScope 2.0.4 的 `Agent` 基于 `asyncio.CancelledError` 实现中断机制，支持在模型推理或工具执行的任意阶段停止执行。中断后上下文保持一致状态，可立即通过新输入继续。

### 中断策略

`AgentscopeService.interrupt()` 根据智能体当前状态和部署拓扑自动选择中断策略：

| 场景 | 智能体状态 | 中断方式 | 说明 |
|---|---|---|---|
| **本地运行中** | 本实例正在执行 `reply_stream` | `task.cancel()` + `interrupt_event.set()` | 直接取消本地 asyncio Task |
| **远程运行中** | 其他实例正在执行 `reply_stream` | Redis Pub/Sub 广播 → 远程实例取消 Task | 跨实例协调，详见 [多实例中断协调](#多实例中断协调) |
| **暂停中** | 等待用户确认 / 外部工具执行 (HITL) | 向 `reply_stream` 传入 `UserInterruptEvent` | 无活跃 Task，通过事件注入中断 |
| **无活跃** | 无运行任务且无待处理工具调用 | 空操作 (noop) | 无 |

#### 场景 1：中断本地运行中的智能体

```
interrupt(conversation_id)
  │
  ├─ 从 _running_tasks 查找活跃 Task
  │
  ├─ task.cancel()  ──────→  CancelledError 抛入 Agent 内部
  │                          │
  │                          ├─ 若在模型推理中:
  │                          │   model._stream() 捕获 CancelledError,
  │                          │   返回 finished_reason=INTERRUPTED,
  │                          │   _reasoning_impl 检测后正常退出
  │                          │
  │                          ├─ 若在并发工具执行中:
  │                          │   _execute_concurrent_tool_calls 捕获,
  │                          │   刷新队列事件, 调用 uncancel()
  │                          │
  │                          └─ CancelledError 传播到 _reply_impl:
  │                              except CancelledError → end_event=INTERRUPTED
  │                              finally → _close_unfinished_tool_calls()
  │                                  + yield AssistantMsg(回退消息)
  │                                  + yield ReplyEndEvent(INTERRUPTED)
  │
  ├─ await task (最多 10s)  ← 等待 Agent finally 块完成持久化
  │
  └─ 返回 {status: interrupted, reason: task_cancelled}
```

**SDK 多层 CancelledError 捕获机制** (Python 3.12 中 CancelledError 只抛出一次)：

1. **模型层** (`model._base.py`): `_stream()` 的 `except asyncio.CancelledError` 捕获后，将 `finished_reason` 设为 `INTERRUPTED` 并 yield 最终 chunk，**不重抛**
2. **并发工具执行** (`_agent.py`): `_execute_concurrent_tool_calls` 的 `except asyncio.CancelledError` 捕获后，刷新队列中已产生的事件 (含 `ToolResultEndEvent(state=INTERRUPTED)`)，调用 `task.uncancel()` 使任务恢复正常，**不重抛**
3. **核心循环** (`_agent.py`): `_reply_impl` 的 `except asyncio.CancelledError` 捕获后设置 `end_event=INTERRUPTED`，`finally` 块调用 `_close_unfinished_tool_calls()` 产出工具结果事件 + 回退消息 + `ReplyEndEvent`，默认**不重抛** (`interruption_raise_cancelled_error=False`)

在以上任何一层，`reply_stream` 生成器都会**正常结束** (而非抛异常)，`chat_stream` 的 `async for` 循环自然退出，进入 `finally` 持久化。

#### 场景 2：中断暂停中的智能体

当智能体因 `RequireUserConfirmEvent` 或 `RequireExternalExecutionEvent` 暂停时，没有活跃的 asyncio Task 可取消。此时通过 `UserInterruptEvent` 恢复并立即中断：

```python
# _interrupt_parked_agent 流程:
state = load_state(conversation_id)         # 从 Redis 加载状态
agent = create_agent_with_state(state)       # 用保存的状态创建 Agent

if not state.has_awaiting_tool_calls(agent.name):
    return noop  # 无待处理工具调用, 无需中断

# 传入 UserInterruptEvent, Agent 清理待处理工具调用后结束
async for event in agent.reply_stream(
    UserInterruptEvent(reply_id=state.reply_id),
):
    collect(event)  # 收集事件 (不向前端 yield)

save_state(conversation_id, agent.state)     # 持久化更新后的状态
save_assistant_message(...)                   # 保存中断消息
```

SDK `_reply_impl` 对 `UserInterruptEvent` 的处理 (短路路径)：
- 检查 `has_awaiting_tool_calls` → 若有，设置 `end_event=INTERRUPTED` 并立即 `return`
- `finally` 块调用 `_close_unfinished_tool_calls()`，为每个待处理工具调用合成 `ToolResultBlock(state=INTERRUPTED)`，产出完整事件生命周期 (`ToolResultStartEvent` → `ToolResultTextDeltaEvent` → `ToolResultEndEvent`)
- 产出回退 `AssistantMsg` + `ReplyEndEvent(INTERRUPTED)`

### 恢复中断的会话

中断后 `AgentState` 和消息已持久化 (Redis + DB)，恢复方式：

| 恢复方式 | 调用 | 说明 |
|---|---|---|
| **继续对话** | `chat_stream(user_message, conversation_id)` | 传入新消息，Agent 从保存的上下文继续 |
| **恢复推理** | `chat_stream(None, conversation_id)` | `user_message=None`，Agent 从当前状态继续推理 (不添加新消息) |
| **交互恢复** | `chat_stream_interactive(confirm_results, conversation_id)` | 传入工具确认结果，继续被暂停的 reply |

恢复时 `chat_stream` 的状态加载逻辑：

```
load_state(session_id)  →  Redis 命中?
  ├─ 是: 直接使用 Redis 中的 AgentState (毫秒级)
  └─ 否: 从 DB t_message 加载历史 → agent.observe(history) 重建 state
```

### chat_stream 中的自动确认机制

`chat_stream` (非交互模式) 使用 `PermissionMode.BYPASS`，但若上一轮因异常中断留下了 `ASKING` 状态的工具调用，下一次 `reply_stream(None)` 会报错 "Agent is waiting for tool calls"。

`_auto_confirm_pending_tool_calls` 在 `reply_stream` 前自动将遗留的 `ASKING` 工具调用设为 `ALLOWED`，避免会话卡死：

```python
# chat_stream 步骤 5
for tc in last_msg.get_content_blocks("tool_call"):
    if tc.state == ToolCallState.ASKING:
        agent._update_tool_call_state(tc.id, ToolCallState.ALLOWED)
```

> **注意**: `chat_stream_interactive` (交互模式) 不会自动确认，而是自动**拒绝**遗留的 `ASKING` 工具调用 (`ToolCallState.FINISHED`)，让 LLM 看到错误结果后重新决策。

### 中断 API

**请求** (`InterruptRequest`)：

```json
{
  "conversation_id": "9f4fe10c-4669-45f7-b950-5b62994c4a15"
}
```

**响应**：

```json
// 本地运行中 → 中断成功
{
  "status": "interrupted",
  "conversation_id": "...",
  "reason": "task_cancelled"
}

// 远程运行中 → 中断成功 (跨实例)
{
  "status": "interrupted",
  "conversation_id": "...",
  "reason": "remote_interrupted"
}

// 暂停中 → 中断成功
{
  "status": "interrupted",
  "conversation_id": "...",
  "reason": "parked_interrupted"
}

// 无活跃会话
{
  "status": "noop",
  "conversation_id": "...",
  "reason": "no_active_session"
}

// 无待处理工具调用
{
  "status": "noop",
  "conversation_id": "...",
  "reason": "not_parked"
}
```

### 中断时序图

```
前端                    FastAPI                 AgentScope SDK
 │                        │                        │
 │  POST /chat/stream     │                        │
 │───────────────────────>│                        │
 │                        │  reply_stream(msg)     │
 │                        │───────────────────────>│
 │  SSE: text_delta...    │  yield event           │
 │<───────────────────────│<───────────────────────│
 │                        │                        │
 │  POST /chat/interrupt  │                        │
 │───────────────────────>│                        │
 │                        │  task.cancel()         │
 │                        │───────────────────────>│
 │                        │                        │ CancelledError 捕获
 │                        │                        │ _close_unfinished_tool_calls()
 │  SSE: tool_result_end  │  yield INTERRUPTED evt │
 │  (state=INTERRUPTED)   │<───────────────────────│
 │  SSE: reply_end        │                        │
 │  (INTERRUPTED)         │                        │
 │<───────────────────────│                        │
 │                        │  finally: save_state   │
 │                        │           save_msg     │
 │                        │           commit DB    │
 │  200 {interrupted}     │                        │
 │<───────────────────────│                        │
 │                        │                        │
 │  POST /chat/stream     │  (恢复对话)             │
 │  {message, conv_id}    │                        │
 │───────────────────────>│                        │
```

---

## 多实例中断协调

### 问题背景

`_running_tasks` (dict[str, asyncio.Task]) 和 `_interrupt_events` (dict[str, asyncio.Event]) 都是进程内存中的数据结构。多实例部署时（如通过 Nginx/K8s 负载均衡），实例 A 收到的 `POST /chat/interrupt` 请求无法触达实例 B 上正在运行的 `asyncio.Task`，因为 `asyncio.Task` 和 `asyncio.Event` 是进程绑定的，无法跨进程共享。

### 解决方案

引入 `InterruptCoordinator` 单例（`app/service/agentscope/interrupt_coordinator.py`），基于 Redis Pub/Sub 实现跨实例的中断请求广播与响应回传。

### 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        Redis                                     │
│                                                                   │
│  ┌─ interrupt:task:{session_id} = instance_A (TTL 300s) ─────┐   │
│  └─ interrupt:res:{session_id}:{req_instance} (响应回传) ─────┘   │
│                                                                   │
│  ┌─ Pub/Sub Channel: agentscope:interrupt:channel ───────────┐   │
│  │  实例 A 广播 "请中断 session_xxx"                           │   │
│  │  实例 B 收到 → 检查本地 _running_tasks → 执行中断 → 回传结果 │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### 核心流程

```
interrupt(conversation_id) 在 实例A 被调用
  │
  ├─ 1. 查本地 _running_tasks[conversation_id]
  │     ├─ 命中 → _cancel_and_wait_task() → 返回 (快速路径, 不变)
  │     └─ 未命中 → 进入远程中断流程
  │
  ├─ 2. 在 Redis 写入占位 key (表示 "正在等待响应")
  │      interrupt:res:{conversation_id}:{instance_A_id} = ""
  │
  ├─ 3. 通过 Pub/Sub 广播中断请求
  │      PUBLISH agentscope:interrupt:channel "{conversation_id}"
  │
  │     ┌─── 实例 B (运行该会话的实例) ──────────────────────┐
  │     │  后台监听器 _listen() 收到消息                      │
  │     │    ├─ 查本地 _running_tasks[conversation_id]        │
  │     │    │   └─ 命中 → 执行中断                           │
  │     │    │       ├─ interrupt_event.set()                 │
  │     │    │       ├─ task.cancel()                         │
  │     │    │       ├─ await task (最多 10s)                 │
  │     │    │       └─ _unregister_task()                    │
  │     │    │                                                │
  │     │    └─ 通过 SCAN 找到所有等待的占位 key               │
  │     │       └─ SET interrupt:res:{id}:{req} = 结果 JSON   │
  │     └────────────────────────────────────────────────────┘
  │
  ├─ 4. 轮询 interrupt:res:{conversation_id}:{instance_A_id}
  │     ├─ 收到结果 → 返回给调用方
  │     └─ 超时 (10s) → 返回 None
  │
  └─ 5. 远程中断返回 None → 降级到 _interrupt_parked_agent()
        (检查是否有暂停中的智能体可中断)
```

### Redis Key 设计

| Key 模式 | 值 | TTL | 说明 |
|---|---|---|---|
| `interrupt:task:{session_id}` | instance_id (8 位 UUID) | 300s | 任务注册: 标识哪个实例正在运行该会话的 reply |
| `interrupt:res:{session_id}:{req_instance_id}` | 结果 JSON 或空字符串 | 30s | 中断响应回传: 请求方预写空占位符, 执行方覆写为实际结果 |

**Pub/Sub Channel**:
- 频道名: `agentscope:interrupt:channel`
- 消息体: `"{conversation_id}"` (纯字符串)

### 新增文件

#### `app/service/agentscope/interrupt_coordinator.py`

`InterruptCoordinator` 单例类，核心职责：

| 方法 | 说明 |
|---|---|
| `start()` | 初始化两个独立 Redis 连接 (一个用于普通命令, 一个用于 Pub/Sub 监听)，启动后台 `_listen()` 任务 |
| `stop()` | 取消后台监听, 释放 Redis 连接 |
| `register_task(session_id)` | 在 Redis 中写入 `interrupt:task:{session_id}` = instance_id (TTL 300s) |
| `deregister_task(session_id)` | 删除 `interrupt:task:{session_id}` |
| `request_remote_interrupt(session_id)` | 发起远程中断: 写占位 key → Pub/Sub 广播 → 轮询响应 (10s 超时) |
| `_listen()` | 后台 asyncio Task: 订阅 `agentscope:interrupt:channel`, 收到消息后处理远程中断 |
| `_handle_remote_interrupt(session_id)` | 检查本地是否有该会话任务, 有则执行中断并回传结果 |

**设计要点**:

1. **两个独立 Redis 连接**: Pub/Sub 的 `listen()` 会阻塞当前连接 (从连接池借用一个连接并独占), 因此普通命令操作 (get/set/scan) 使用另一个独立的 `Redis` 客户端实例, 避免相互阻塞。

2. **请求-响应模式**: Pub/Sub 是 fire-and-forget 的, 请求方无法直接获得处理结果。因此使用 Redis key 作为响应通道:
   - 请求方预先写入 `interrupt:res:{session_id}:{self_instance_id} = ""` (占位符)
   - 执行方通过 `SCAN` 通配符查询 `interrupt:res:{session_id}:*`, 将每个匹配的 key 覆写为实际结果 JSON
   - 请求方轮询自己的 key, 值变为非空即表示收到响应

3. **实例 ID**: 每个实例启动时生成 8 位 UUID, 用于标识 "谁在运行哪个任务" 和 "谁在等待哪个响应"。

### 涉及改动的文件

| 文件 | 改动 |
|------|------|
| `app/service/agentscope/interrupt_coordinator.py` | **新增** — 跨实例中断协调器完整实现 |
| `app/service/agentscope/agentscope_service.py` | `_register_task()` / `_unregister_task()` 增加 Redis 任务注册; `interrupt()` 增加远程中断分支; 新增 import |
| `app/main.py` | lifespan 中增加 `InterruptCoordinator.get_instance().start()` / `stop()` |

### 中断请求路由决策

```python
# agentscope_service.py → interrupt() 方法
async def interrupt(self, conversation_id: str) -> dict:
    # 1. 本地运行中 → 直接取消 (快速路径, 与单实例行为一致)
    task = self._running_tasks.get(conversation_id)
    if task is not None and not task.done():
        return await self._cancel_and_wait_task(conversation_id, task)

    # 2. 远程运行中 → 通过 Redis Pub/Sub 请求中断
    coordinator = InterruptCoordinator.get_instance()
    remote_result = await coordinator.request_remote_interrupt(conversation_id)
    if remote_result is not None:
        return remote_result

    # 3. 无运行任务 → 尝试中断暂停中的智能体 (parked)
    return await self._interrupt_parked_agent(conversation_id)
```

### 单实例兼容性

- Redis 不可用时 (`redis` 包未安装或 Redis 服务不可达): 协调器 `start()` 捕获异常后静默跳过, 后台监听不启动, 所有 `register_task` / `deregister_task` / `request_remote_interrupt` 调用检测到 `self._redis is None` 后直接返回, 不影响核心对话流程
- 单实例部署: `interrupt()` 始终走步骤 1 (本地快速路径), 不会触发 Redis 交互, 行为与改动前完全一致

### 跨实例中断时序图

```
前端                  实例A (收到中断请求)       Redis              实例B (运行该会话)
 │                        │                        │                       │
 │  POST /chat/interrupt  │                        │                       │
 │───────────────────────>│                        │                       │
 │                        │ ① 查本地 _running_tasks│                       │
 │                        │    → 未命中             │                       │
 │                        │                        │                       │
 │                        │ ② SET interrupt:res:    │                       │
 │                        │    {id}:{instA} = ""    │                       │
 │                        │───────────────────────>│                       │
 │                        │                        │                       │
 │                        │ ③ PUBLISH interrupt:    │                       │
 │                        │    channel "{id}"       │                       │
 │                        │───────────────────────>│                       │
 │                        │                        │  ④ 推送消息给订阅者     │
 │                        │                        │──────────────────────>│
 │                        │                        │                       │ ⑤ 检查本地
 │                        │                        │                       │   _running_tasks
 │                        │                        │                       │   → 命中
 │                        │                        │                       │
 │                        │                        │                       │ ⑥ interrupt_event.set()
 │                        │                        │                       │   task.cancel()
 │                        │                        │                       │   await task (最多10s)
 │                        │                        │                       │
 │                        │                        │  ⑦ SCAN interrupt:res: │
 │                        │                        │    {id}:* → SET 结果    │
 │                        │                        │<──────────────────────│
 │                        │                        │                       │
 │                        │ ⑧ GET interrupt:res:    │                       │
 │                        │    {id}:{instA}         │                       │
 │                        │───────────────────────>│                       │
 │                        │    返回结果 JSON        │                       │
 │                        │<───────────────────────│                       │
 │                        │                        │                       │
 │  200 {status:"interrupted"}                     │                       │
 │<───────────────────────│                        │                       │
```

### 故障场景处理

| 场景 | 行为 |
|---|---|
| **远程实例崩溃** | `interrupt:task:{id}` TTL 过期 (300s) 自动清理; 请求方 10s 超时后降级到 parked 中断 |
| **Redis 不可用** | `register_task` / `deregister_task` 静默失败 (日志 warning); `request_remote_interrupt` 返回 None, 降级到 parked 中断 |
| **Pub/Sub 断连** | `_listen()` 5s 后自动重连并重新订阅; 断连期间丢失的中断请求不会重发, 请求方 10s 超时降级 |
| **多实例同时中断同一会话** | 第一个 `interrupt_event.set()` + `task.cancel()` 生效; 后续请求发现 task 已 done/不存在, 各自降级到 parked 中断 (返回 noop) |

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
    await InterruptCoordinator             # 启动跨实例中断协调器 (Pub/Sub 监听)
        .get_instance().start()
    yield
    # 关闭
    await InterruptCoordinator             # 停止中断协调器 (释放 Redis 连接)
        .get_instance().stop()
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
