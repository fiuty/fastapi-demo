"""
模型重试/回退通知中间件

通过 AgentScope 的 ``on_model_call`` hook 捕获每次模型调用失败,
将重试/回退信息写入 ``agent.state.middle_context`` 中的通知列表,
供上层服务事件循环读取并转发为 SSE 事件给前端。
"""
import logging
from typing import Any

from agentscope.middleware import MiddlewareBase

logger = logging.getLogger("agentscope")

_RETRY_STATE_KEY = "_retry_state"
_RETRY_NOTIFICATIONS_KEY = "_retry_notifications"


class ModelRetryNotifierMiddleware(MiddlewareBase):
    """在模型调用失败时将重试/回退信息写入 AgentState 通知列表。

    不执行任何实际重试逻辑 —— 仅观察并记录。
    上层服务循环在消费 Agent 事件流时主动 drain 通知列表并转发到前端。

    使用方式:

        agent = Agent(
            ...
            middlewares=[ModelRetryNotifierMiddleware()],
            ...
        )
    """

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ) -> Any:
        """onion hook: AgentScope 每次调用模型(含重试)都会经过此方法。

        ``next_handler(**input_kwargs)`` 内部执行实际的模型 API 调用，
        可能是外层 ``Agent._call_model`` 重试循环中的某一次尝试。
        本方法不修改调用行为，仅观察成败并写入通知队列。
        """
        # ---- 获取当前正被调用的模型 ----
        model = input_kwargs["current_model"]
        model_name = model.model

        # ---- 跟踪状态: 记录每个模型被调用了多少次 ----
        # middle_context 是 AgentState 上的一个 dict,按 key 隔离各中间件状态
        # 每轮 reply 开始时 agentscope_service 会 pop 清理 _retry_state 和
        # _retry_notifications,确保不同 reply 之间状态不混淆
        retry_state = agent.state.middle_context.setdefault(
            _RETRY_STATE_KEY, {},
        )

        # _tracked: {model_name: {"attempts": N}} 记录每个模型在本轮 reply 中
        # 被 on_model_call 调用的次数
        tracked = retry_state.setdefault("_tracked", {})
        if model_name not in tracked:
            tracked[model_name] = {"attempts": 0}
        tracked_info = tracked[model_name]
        tracked_info["attempts"] += 1

        # ---- 检测是否切换到了备用模型 ----
        # 条件: tracked 中已存在 >1 个模型,且当前模型是首次被调用
        # 此时说明 _call_model 已经遍历完了主模型的所有重试,开始尝试 fallback
        is_fallback = len(tracked) > 1 and tracked_info["attempts"] == 1
        if is_fallback:
            notifications = agent.state.middle_context.setdefault(
                _RETRY_NOTIFICATIONS_KEY, [],
            )
            notifications.append({
                "type": "fallback",
                "model_name": model_name,
                "message": f"主模型不可用，切换到备用模型: {model_name}",
            })

        try:
            # ---- 执行实际的模型调用 ----
            # next_handler 会依次经过后续中间件链,最终调用 await model(...)
            result = await next_handler(**input_kwargs)

            # ---- 调用成功 ----
            # 如果该模型之前失败过 (attempts > 1),说明是经过重试后才成功的,
            # 发送 recovery 通知告知前端模型已恢复
            if tracked_info["attempts"] > 1:
                notifications = agent.state.middle_context.setdefault(
                    _RETRY_NOTIFICATIONS_KEY, [],
                )
                notifications.append({
                    "type": "recovery",
                    "model_name": model_name,
                    "message": f"模型 {model_name} 第 {tracked_info['attempts']} 次调用成功",
                })
            return result

        except Exception as e:
            # ---- 调用失败 ----
            # 异常会继续向外抛给 _call_model 的重试循环,
            # AgentScope 会根据 max_retries 决定是重试当前模型还是切换到 fallback
            notifications = agent.state.middle_context.setdefault(
                _RETRY_NOTIFICATIONS_KEY, [],
            )
            notifications.append({
                "type": "retry",
                "model_name": model_name,
                "attempt": tracked_info["attempts"],
                "is_fallback": is_fallback,
                "error": str(e)[:300],
                "message": (
                    f"备用模型 {model_name} 调用失败，正在重试..."
                    if is_fallback
                    else f"模型 {model_name} 调用失败，正在重试 ({tracked_info['attempts']}/3)..."
                ),
            })
            raise
