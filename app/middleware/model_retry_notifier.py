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
        model = input_kwargs["current_model"]
        model_name = model.model

        retry_state = agent.state.middle_context.setdefault(
            _RETRY_STATE_KEY, {},
        )

        tracked = retry_state.setdefault("_tracked", {})
        if model_name not in tracked:
            tracked[model_name] = {"attempts": 0}
        tracked_info = tracked[model_name]
        tracked_info["attempts"] += 1

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
            result = await next_handler(**input_kwargs)
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
