from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SessionContext:
    active_order_no: str | None
    pending_intent: str | None
    updated_at: datetime
    expires_at: datetime
