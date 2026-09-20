import re
from datetime import datetime, timedelta, timezone

from app.agents.supervisor import SupervisorRouter
from app.domain.session_context import SessionContext
from app.nodes.order import ORDER_NO_PATTERN
from app.ports.session_context import SessionContextRepository


ORDER_ROUTES = {"order_query", "logistics_tracking"}
AMBIGUOUS_REFERENCE = re.compile(
    r"另一个|另一单|另外|其他订单|其它订单|新订单|前一个|上一个|上一单|上个订单|前一单|之前那个|"
    r"它们|这些订单|那些订单|那几个|这几个|两个订单|两笔订单|两单|多个订单|分别|other order|previous order",
    re.I,
)
VAGUE_REFERENCE = re.compile(r"(?:那)?(?:它|它们|这个|那个)(?:呢|怎么样)[？?。\s]*")


class SessionContextService:
    def __init__(self, repository: SessionContextRepository, *, ttl_minutes: int = 30):
        self.repository = repository
        self.ttl = timedelta(minutes=ttl_minutes)

    def load(self, *, shop_id: str, user_id: int, session_id: str) -> dict:
        context = self.repository.get(shop_id=shop_id, user_id=user_id, session_id=session_id)
        if context is None:
            return {}
        if context.expires_at <= datetime.now(timezone.utc):
            return {"context_expired": True}
        return {"active_order_no": context.active_order_no, "pending_intent": context.pending_intent}

    def remember(self, *, shop_id: str, user_id: int, session_id: str, result: dict) -> None:
        numbers = list(dict.fromkeys(ORDER_NO_PATTERN.findall(result["message"].upper())))
        route = result["route"]
        # A new explicit object replaces the old one even if lookup is denied.
        active = numbers[0] if len(numbers) == 1 else result.get("resolved_order_no")
        if len(numbers) > 1 or result.get("order_context_error") or route not in ORDER_ROUTES:
            active = None
        pending = route if route in ORDER_ROUTES and active is None else None
        now = datetime.now(timezone.utc)
        self.repository.save(
            shop_id=shop_id, user_id=user_id, session_id=session_id,
            context=SessionContext(active, pending, now, now + self.ttl),
        )


def resolve_order_context(state: dict, route: str) -> dict:
    if route not in ORDER_ROUTES:
        return {}
    message = state["message"]
    numbers = list(dict.fromkeys(ORDER_NO_PATTERN.findall(message.upper())))
    if len(numbers) > 1:
        return {"order_context_error": "消息中有多个订单，请确认本次要查询的一个订单号。"}
    if numbers:
        return {"resolved_order_no": numbers[0]}
    if "vlt-" in message.casefold():
        return {"order_context_error": "请重新提供格式为 VLT-YYYY-NNNN 的完整订单号。"}
    if AMBIGUOUS_REFERENCE.search(message) or VAGUE_REFERENCE.fullmatch(message.strip()):
        return {"order_context_error": "无法确认您指的是哪个订单，请提供本次要查询的订单号。"}
    if state.get("context_expired"):
        return {"order_context_error": "订单上下文已过期，请重新提供订单号。"}
    # A model route alone is not sufficient evidence to reuse a previous object.
    if SupervisorRouter._route_with_rules(message).route not in ORDER_ROUTES:
        return {"order_context_error": "请提供订单号，并确认要查询订单状态还是物流。"}
    return {"resolved_order_no": state.get("active_order_no")}
