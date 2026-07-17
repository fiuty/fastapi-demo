"""
模型重试/回退通知中间件

通过 AgentScope 的 ``on_model_call`` hook 捕获每次模型调用失败,
将重试/回退信息写入一个 ``asyncio.Queue``,
供上层合并流生成器实时读取并转发为 SSE 事件给前端。

与之前基于 list + drain 的方案不同: 使用 Queue 可实现
每次重试事件即时推送到前端, 不会积压后在 Agent 事件恢复时批量到达。
"""
import logging
from typing import Any

from agentscope.middleware import MiddlewareBase

logger = logging.getLogger("agentscope")

_RETRY_STATE_KEY = "_retry_state"
_RETRY_QUEUE_KEY = "_retry_queue"


class ModelRetryNotifierMiddleware(MiddlewareBase):
    """在模型调用失败时将重试/回退信息写入 asyncio.Queue。

    不执行任何实际重试逻辑 —— 仅观察并记录。
    合并流生成器通过 ``asyncio.wait`` 同时监听 Agent 事件、中断信号
    和 retry_queue, 实现重试事件的实时推送。

    使用方式:

        agent = Agent(
            ...
            middlewares=[ModelRetryNotifierMiddleware()],
            ...
        )
    """

    @staticmethod
    def get_retry_queue(agent: Any) -> "asyncio.Queue | None":
        """从 AgentState 中取出重试通知队列。

        由合并流生成器在上层创建 Queue 并注入到
        ``agent.state.middle_context["_retry_queue"]``,
        中间件通过此方法读取。
        """
        import asyncio
        return agent.state.middle_context.get(_RETRY_QUEUE_KEY)

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ) -> Any:
        """onion hook: AgentScope 每次调用模型(含重试)都会经过此方法。

        ``next_handler(**input_kwargs)`` 内部执行实际的模型 API 调用，
        可能是外层 ``Agent._call_model`` 重试循环中的某一次尝试。
        本方法不修改调用行为，仅观察成败并通过 retry_queue 实时通知前端。
        """
        retry_queue = self.get_retry_queue(agent)

        # ---- 获取当前正被调用的模型 ----
        model = input_kwargs["current_model"]
        model_name = model.model

        # ---- 跟踪状态: {model_name: count}, 记录每个模型被调用的次数 ----
        # middle_context 是 AgentState 上的一个 dict,按 key 隔离各中间件状态
        # 每轮 reply 开始时 agentscope_service 会 pop 清理 _retry_state,
        # 确保不同 reply 之间状态不混淆
        retry_state = agent.state.middle_context.setdefault(_RETRY_STATE_KEY, {})
        count = retry_state.get(model_name, 0) + 1
        retry_state[model_name] = count

        # ---- 检测是否切换到了备用模型 ----
        # 条件: retry_state 中已存在 >1 个模型,且当前模型是首次被调用
        # 此时说明 _call_model 已经遍历完了主模型的所有重试,开始尝试 fallback
        is_fallback = len(retry_state) > 1 and count == 1

        def _enqueue(notification: dict) -> None:
            """将通知放入 retry_queue (非阻塞, 队列满则丢弃警告)。"""
            if retry_queue is None:
                return
            try:
                retry_queue.put_nowait(notification)
            except Exception:
                logger.warning("retry_queue 写入失败 (可能已满)")

        if is_fallback:
            _enqueue({
                "type": "fallback",
                "model_name": model_name,
                "message": f"主模型不可用，切换到备用模型: {model_name}",
            })

        try:
            # ---- 执行实际的模型调用 ----
            # next_handler 会依次经过后续中间件链,最终调用 await model(...)
            result = await next_handler(**input_kwargs)

            # ---- 调用成功 ----
            # 如果该模型之前失败过 (count > 1),说明是经过重试后才成功的,
            # 发送 recovery 通知告知前端模型已恢复
            if count > 1:
                _enqueue({
                    "type": "recovery",
                    "model_name": model_name,
                    "message": f"模型 {model_name} 第 {count} 次调用成功",
                })
            return result

        except Exception as e:
            # ---- 调用失败 ----
            # 异常会继续向外抛给 _call_model 的重试循环,
            # AgentScope 会根据 max_retries 决定是重试当前模型还是切换到 fallback
            _enqueue({
                "type": "retry",
                "model_name": model_name,
                "attempt": count,
                "is_fallback": is_fallback,
                "error": str(e)[:300],
                "message": (
                    f"备用模型 {model_name} 调用失败，正在重试..."
                    if is_fallback
                    else f"模型 {model_name} 调用失败，正在重试 ({count}/3)..."
                ),
            })
            raise
