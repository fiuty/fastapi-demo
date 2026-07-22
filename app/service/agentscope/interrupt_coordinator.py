"""
跨实例中断协调器: 基于 Redis Pub/Sub 实现多实例部署下的对话中断。

问题背景:
  _running_tasks (dict[str, asyncio.Task]) 和 _interrupt_events (dict[str, asyncio.Event])
  都是进程内存中的数据结构。多实例部署时, 实例 A 收到的中断请求无法触达实例 B 上
  正在运行的对话任务。

设计:
  1. 任务注册: 对话开始时在 Redis 写入 "哪个实例在运行哪个会话" (带 TTL)
  2. 中断通知: 通过 Redis Pub/Sub 广播中断请求给所有实例
  3. 本地处理: 收到广播后, 运行该任务的实例执行实际中断
  4. 响应回传: 处理结果通过 Redis key 回传给请求方

Redis Key 设计:
  interrupt:task:{session_id}              → instance_id (TTL 300s)
  interrupt:res:{session_id}:{req_inst}    → result JSON (TTL 30s)

Pub/Sub Channel:
  agentscope:interrupt:channel
"""
import asyncio
import json
import logging
import uuid
from typing import Optional

from app.config import settings

logger = logging.getLogger("agentscope")

# Pub/Sub 频道名
INTERRUPT_CHANNEL = "agentscope:interrupt:channel"

# Redis key 前缀
KEY_PREFIX_TASK = "interrupt:task"
KEY_PREFIX_RESULT = "interrupt:res"

# 任务注册 TTL (秒) — 对话任务最多存活 5 分钟, 超时自动清理
TASK_TTL = 300
# 中断响应 TTL (秒) — 中断请求方等待响应的时间
RESULT_TTL = 30


class InterruptCoordinator:
    """Redis 跨实例中断协调器 (单例)。"""

    _instance: Optional["InterruptCoordinator"] = None

    def __init__(self) -> None:
        self._instance_id: str = str(uuid.uuid4())[:8]
        self._redis: Optional["Redis"] = None  # type: ignore[name-defined]
        self._pubsub: Optional["Redis"] = None  # type: ignore[name-defined]
        self._listener_task: Optional[asyncio.Task] = None

    # ======================== 生命周期 ========================

    @classmethod
    def get_instance(cls) -> "InterruptCoordinator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def instance_id(self) -> str:
        return self._instance_id

    async def start(self) -> None:
        """启动协调器: 建立 Redis Pub/Sub 连接并启动后台监听。"""
        try:
            from redis.asyncio import Redis
        except ImportError:
            logger.warning(
                "redis 包未安装, 中断协调器不可用 (仅支持单实例部署)"
            )
            return

        password = settings.REDIS_PASSWORD or None
        self._redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=password,
            decode_responses=True,
        )
        self._pubsub = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=password,
            decode_responses=True,
        )

        self._listener_task = asyncio.create_task(self._listen())
        logger.info(
            "中断协调器已启动 | instance_id=%s | channel=%s",
            self._instance_id, INTERRUPT_CHANNEL,
        )

    async def stop(self) -> None:
        """关闭协调器: 取消后台监听, 释放连接。"""
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

        logger.info("中断协调器已关闭 | instance_id=%s", self._instance_id)

    # ======================== 后台监听 ========================

    async def _listen(self) -> None:
        """后台任务: 订阅 Pub/Sub 频道并处理收到的中断请求。

        使用 redis-py PubSub 模式, 该模式会从连接池中借用一个连接并独占使用,
        因此必须使用独立的 Redis 客户端实例 (self._pubsub)。
        异常时关闭旧 PubSub 对象再重连, 避免连接泄漏。
        """
        while True:
            pubsub = None
            try:
                pubsub = self._pubsub.pubsub()
                await pubsub.subscribe(INTERRUPT_CHANNEL)
                logger.info(
                    "中断监听器已订阅 | instance_id=%s", self._instance_id,
                )
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    session_id = message["data"]
                    if not session_id:
                        continue
                    asyncio.create_task(
                        self._handle_remote_interrupt(session_id),
                    )
            except asyncio.CancelledError:
                if pubsub is not None:
                    await pubsub.close()
                raise
            except Exception:
                logger.exception("中断监听器异常, 5s 后重连")
                if pubsub is not None:
                    try:
                        await pubsub.close()
                    except Exception:
                        pass
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    raise

    async def _handle_remote_interrupt(self, session_id: str) -> None:
        """处理从 Pub/Sub 收到的远程中断请求。

        检查本地是否有该会话的运行中任务, 若有则执行中断并通过 Redis key 回传结果。
        """
        from app.service.agentscope.agentscope_service import AgentscopeService

        task = AgentscopeService._running_tasks.get(session_id)
        if task is None or task.done():
            return

        logger.info(
            "收到远程中断请求 | session_id=%s | instance_id=%s",
            session_id, self._instance_id,
        )

        # 执行中断: 设置中断信号 + 取消任务 + 等待清理
        interrupt_event = AgentscopeService._get_interrupt_event(session_id)
        interrupt_event.set()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            logger.warning(
                "远程中断等待清理完成/超时 | session_id=%s", session_id,
            )

        # 清理本地状态 + 显式 await Redis 注销 (兜底: 若 task 超时未清理)
        AgentscopeService._running_tasks.pop(session_id, None)
        AgentscopeService._interrupt_events.pop(session_id, None)
        await self.deregister_task(session_id)

        result = {
            "status": "interrupted",
            "conversation_id": session_id,
            "reason": "remote_interrupted",
        }

        await self._respond_to_all_requesters(session_id, result)

    async def _respond_to_all_requesters(
        self, session_id: str, result: dict,
    ) -> None:
        """将中断结果回写给所有等待该会话中断结果的请求方。

        请求方在发送中断请求时会预写入占位 key:
          interrupt:res:{session_id}:{requester_instance_id} = ""
        本方法通过 SCAN 通配符找到所有匹配的 key, 覆写为实际结果 JSON。
        请求方轮询到非空值即表示收到响应。
        """
        result_json = json.dumps(result, ensure_ascii=False)
        pattern = f"{KEY_PREFIX_RESULT}:{session_id}:*"
        try:
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor=cursor, match=pattern, count=20,
                )
                for key in keys:
                    await self._redis.set(key, result_json, ex=RESULT_TTL)
                if cursor == 0:
                    break
        except Exception:
            logger.exception(
                "回传中断响应失败 | session_id=%s", session_id,
            )

    # ======================== 任务注册/注销 ========================

    async def register_task(self, session_id: str) -> None:
        """在 Redis 中注册: 本实例正在运行该会话。
        调用方应在 _register_task 时调用此方法。
        """
        if self._redis is None:
            return
        key = f"{KEY_PREFIX_TASK}:{session_id}"
        try:
            await self._redis.set(key, self._instance_id, ex=TASK_TTL)
            logger.debug(
                "注册运行任务 | session_id=%s | instance_id=%s",
                session_id, self._instance_id,
            )
        except Exception:
            logger.exception("注册运行任务失败 | session_id=%s", session_id)

    async def deregister_task(self, session_id: str) -> None:
        """在 Redis 中注销任务。
        调用方应在 _unregister_task 时调用此方法。
        """
        if self._redis is None:
            return
        key = f"{KEY_PREFIX_TASK}:{session_id}"
        try:
            await self._redis.delete(key)
            logger.debug(
                "注销运行任务 | session_id=%s", session_id,
            )
        except Exception:
            logger.exception("注销运行任务失败 | session_id=%s", session_id)

    async def get_task_instance(self, session_id: str) -> Optional[str]:
        """查询运行指定会话任务的实例 ID。"""
        if self._redis is None:
            return None
        key = f"{KEY_PREFIX_TASK}:{session_id}"
        try:
            return await self._redis.get(key)
        except Exception:
            return None

    # ======================== 跨实例中断 ========================

    async def request_remote_interrupt(
        self, session_id: str,
    ) -> Optional[dict]:
        """发送远程中断请求并等待结果。

        1. 检查 Redis 中是否有实例注册了该会话任务, 无则立即返回 None
        2. 在 Redis 中预写入一个空结果占位 key (表示"正在等待")
        3. 通过 Pub/Sub 广播中断请求
        4. 轮询等待结果 (最多 10s)
        5. 返回结果或 None (超时)

        Returns:
            dict: 中断结果; None 表示无远程任务/超时/失败
        """
        if self._pubsub is None or self._redis is None:
            return None

        # 先检查是否有实例注册了该会话任务, 避免无任务时白白等待 10s
        owner = await self.get_task_instance(session_id)
        if owner is None:
            return None
        # 任务在本实例 (不应该走到这里, interrupt() 已先检查本地)
        if owner == self._instance_id:
            return None

        result_key = f"{KEY_PREFIX_RESULT}:{session_id}:{self._instance_id}"

        # 预写入占位 key: 运行任务的实例扫描到此 key 后回写结果
        try:
            await self._redis.set(result_key, "", ex=RESULT_TTL)
        except Exception:
            logger.exception("写入中断占位 key 失败")
            return None

        # 广播中断请求
        try:
            await self._pubsub.publish(INTERRUPT_CHANNEL, session_id)
            logger.info(
                "已广播中断请求 | session_id=%s | requester=%s",
                session_id, self._instance_id,
            )
        except Exception:
            logger.exception("广播中断请求失败")
            await self._redis.delete(result_key)
            return None

        # 轮询等待结果
        try:
            for _ in range(20):  # 20 次 × 500ms = 10s max
                raw = await self._redis.get(result_key)
                if raw:  # 非空字符串表示已收到结果
                    result = json.loads(raw)
                    await self._redis.delete(result_key)
                    logger.info(
                        "收到远程中断响应 | session_id=%s | status=%s",
                        session_id, result.get("status"),
                    )
                    return result
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("等待中断响应异常")

        # 超时: 清理占位 key
        try:
            await self._redis.delete(result_key)
        except Exception:
            pass
        logger.warning(
            "远程中断请求超时 | session_id=%s", session_id,
        )
        return None
