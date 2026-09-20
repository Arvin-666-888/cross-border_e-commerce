from datetime import timezone

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.domain.session_context import SessionContext
from app.models.tables import SessionContextTable


class SQLiteSessionContextRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, *, shop_id: str, user_id: int, session_id: str) -> SessionContext | None:
        row = self.db.get(SessionContextTable, (shop_id, user_id, session_id))
        if row is None:
            return None
        return SessionContext(
            active_order_no=row.active_order_no,
            pending_intent=row.pending_intent,
            updated_at=row.updated_at.replace(tzinfo=timezone.utc),
            expires_at=row.expires_at.replace(tzinfo=timezone.utc),
        )

    def save(
        self, *, shop_id: str, user_id: int, session_id: str, context: SessionContext
    ) -> None:
        values = {
            "active_order_no": context.active_order_no,
            "pending_intent": context.pending_intent,
            "updated_at": context.updated_at,
            "expires_at": context.expires_at,
        }
        statement = insert(SessionContextTable).values(
            shop_id=shop_id, user_id=user_id, session_id=session_id, **values
        )
        self.db.execute(statement.on_conflict_do_update(
            index_elements=["shop_id", "user_id", "session_id"], set_=values
        ))
        # The chat audit commits the context and audit together.
