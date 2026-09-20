from typing import Protocol

from app.domain.session_context import SessionContext


class SessionContextRepository(Protocol):
    def get(self, *, shop_id: str, user_id: int, session_id: str) -> SessionContext | None: ...

    def save(
        self, *, shop_id: str, user_id: int, session_id: str, context: SessionContext
    ) -> None: ...
