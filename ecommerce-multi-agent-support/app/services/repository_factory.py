from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.adapters.sqlite import SQLiteCatalogRepository, SQLiteOrderRepository, SQLiteShipmentRepository
from app.config import settings
from app.ports import CatalogRepository, OrderRepository, ShipmentRepository
from app.adapters.sqlite.session_context import SQLiteSessionContextRepository
from app.ports.session_context import SessionContextRepository


@dataclass(slots=True)
class Repositories:
    catalog: CatalogRepository
    orders: OrderRepository
    shipments: ShipmentRepository
    sessions: SessionContextRepository


def build_repositories(db: Session) -> Repositories:
    if settings.COMMERCE_BACKEND != "sqlite":
        raise RuntimeError("WooCommerce adapter is planned for V3 and is not available in V1")
    return Repositories(
        catalog=SQLiteCatalogRepository(db),
        orders=SQLiteOrderRepository(db),
        shipments=SQLiteShipmentRepository(db),
        sessions=SQLiteSessionContextRepository(db),
    )
