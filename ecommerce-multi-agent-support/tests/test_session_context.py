import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import login_headers
from app.config import settings
from app.database import SessionLocal
from app.models.tables import AuditLogTable, OrderTable, SessionContextTable, ShipmentTable, UserTable


@pytest.fixture(autouse=True)
def local_routing(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "")


def chat(client, headers, message, session="memory", endpoint="/api/v1/chat"):
    response = client.post(endpoint, headers=headers, json={"session_id": session, "message": message})
    assert response.status_code == 200
    return response.json()


def assert_missing(data):
    assert data["order_id"] is None
    assert data["order_facts"] is None
    assert data["shipment_facts"] is None
    assert data["tool_trace"] == []
    assert "订单号" in data["answer"]


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/chat/preview"])
def test_followup_queries_latest_facts(client, endpoint):
    headers = login_headers(client, "demo_user_01")
    first = chat(client, headers, "查一下订单 VLT-2026-0001", endpoint=endpoint)
    assert first["order_facts"]["order_no"] == "VLT-2026-0001"
    eta = datetime(2030, 1, 2, 12, tzinfo=timezone.utc)
    with SessionLocal() as db:
        order = db.scalar(select(OrderTable).where(OrderTable.order_no == "VLT-2026-0001"))
        shipment = db.scalar(select(ShipmentTable).where(ShipmentTable.order_id == order.id))
        order.status = "completed"
        shipment.status = "delivered"
        shipment.estimated_delivery_at = eta
        db.commit()
    second = chat(client, headers, "那它什么时候到？", endpoint=endpoint)
    assert second["route"] == second["dispatched_to"] == "logistics_tracking"
    assert second["order_id"] == "VLT-2026-0001"
    assert second["order_facts"]["status"] == "completed"
    assert second["shipment_facts"]["status"] == "delivered"
    assert second["shipment_facts"]["estimated_delivery_at"].startswith("2030-01-02T12:00:00")
    assert second["request_id"] != first["request_id"]
    assert second["tool_trace"][0]["request_id"] == second["request_id"]


def test_explicit_new_order_replaces_old_even_when_denied(client):
    headers = login_headers(client, "demo_user_01")
    chat(client, headers, "查一下订单 VLT-2026-0001")
    second = chat(client, headers, "查一下订单 VLT-2026-0002")
    assert second["order_id"] == "VLT-2026-0002"
    assert second["order_facts"] is None
    third = chat(client, headers, "那它什么时候到？")
    assert third["order_id"] == "VLT-2026-0002"
    assert third["order_facts"] is None
    assert third["tool_trace"][0]["arguments"] == {"order_no": "VLT-2026-0002"}


def test_explicit_owned_new_order_returns_new_facts(client):
    headers = login_headers(client, "demo_user_01")
    with SessionLocal() as db:
        a = db.scalar(select(OrderTable).where(OrderTable.order_no == "VLT-2026-0001"))
        b = db.scalar(select(OrderTable).where(OrderTable.order_no == "VLT-2026-0002"))
        b.user_id, b.shop_id = a.user_id, a.shop_id
        db.commit()
    chat(client, headers, "查一下订单 VLT-2026-0001")
    second = chat(client, headers, "订单 VLT-2026-0002 什么时候到？")
    assert second["order_facts"]["order_no"] == "VLT-2026-0002"
    assert chat(client, headers, "那它什么时候到？")["order_id"] == "VLT-2026-0002"


def test_same_session_isolated_by_user(client):
    alice = login_headers(client, "demo_user_01")
    bob = login_headers(client, "demo_user_04")
    chat(client, alice, "查一下订单 VLT-2026-0001")
    assert_missing(chat(client, bob, "那它什么时候到？"))
    assert chat(client, alice, "那它什么时候到？")["order_id"] == "VLT-2026-0001"


def test_same_user_and_session_isolated_by_shop(client):
    headers = login_headers(client, "demo_user_01")
    chat(client, headers, "查一下订单 VLT-2026-0001")
    with SessionLocal() as db:
        user = db.scalar(select(UserTable).where(UserTable.username == "demo_user_01"))
        original_shop = user.shop_id
        user.shop_id = "another-shop"
        db.commit()
    other_shop_headers = login_headers(client, "demo_user_01")
    assert_missing(chat(client, other_shop_headers, "那它什么时候到？"))
    with SessionLocal() as db:
        user = db.scalar(select(UserTable).where(UserTable.username == "demo_user_01"))
        user.shop_id = original_shop
        db.commit()
    assert chat(client, headers, "那它什么时候到？")["order_id"] == "VLT-2026-0001"


def test_missing_history_and_different_session(client):
    headers = login_headers(client, "demo_user_01")
    data = chat(client, headers, "那它什么时候到？")
    assert data["route"] == "logistics_tracking"
    assert_missing(data)
    chat(client, headers, "查一下订单 VLT-2026-0001")
    assert_missing(chat(client, headers, "那它什么时候到？", session="different"))


def test_historical_order_rechecks_ownership_without_existence_leak(client):
    headers = login_headers(client, "demo_user_01")
    chat(client, headers, "查一下订单 VLT-2026-0001")
    with SessionLocal() as db:
        order = db.scalar(select(OrderTable).where(OrderTable.order_no == "VLT-2026-0001"))
        order.user_id = db.scalar(select(UserTable.id).where(UserTable.username == "demo_user_02"))
        db.commit()
    denied = chat(client, headers, "那它什么时候到？")
    missing = chat(client, headers, "订单 VLT-2099-9999 什么时候到？", session="missing")
    assert denied["answer"] == missing["answer"] == "订单不存在或无权访问，请检查订单号是否属于当前账号。"
    for data in (denied, missing):
        assert data["order_facts"] is None
        assert data["shipment_facts"] is None
        assert data["tool_trace"][0]["success"] is False
        assert data["tool_trace"][0]["result_count"] == 0


def test_expired_context_not_resurrected(client):
    headers = login_headers(client, "demo_user_01")
    chat(client, headers, "查一下订单 VLT-2026-0001")
    with SessionLocal() as db:
        context = db.scalar(select(SessionContextTable))
        context.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    data = chat(client, headers, "那它什么时候到？")
    assert_missing(data)
    assert "过期" in data["answer"]
    assert_missing(chat(client, headers, "那它什么时候到？"))
    assert chat(client, headers, "订单 VLT-2026-0001 什么时候到？")["order_facts"] is not None


@pytest.mark.parametrize("message", [
    "订单 VLT-2026-0001 和 VLT-2026-0002 什么时候到？",
    "另一个订单什么时候到？",
    "上一单什么时候到？",
    "这两个订单什么时候到？",
    "那它呢？",
    "那它们呢？",
    "订单 VLT-2026-12 什么时候到？",
])
def test_ambiguous_or_malformed_input_never_uses_old_order(client, message):
    headers = login_headers(client, "demo_user_01")
    chat(client, headers, "查一下订单 VLT-2026-0001")
    assert_missing(chat(client, headers, message))
    assert_missing(chat(client, headers, "那它什么时候到？"))


def test_topic_switch_clears_referent(client):
    headers = login_headers(client, "demo_user_01")
    chat(client, headers, "查一下订单 VLT-2026-0001")
    chat(client, headers, "解释一下量子力学")
    assert_missing(chat(client, headers, "那它什么时候到？"))


def test_pending_logistics_accepts_bare_number(client):
    headers = login_headers(client, "demo_user_01")
    assert_missing(chat(client, headers, "那它什么时候到？"))
    data = chat(client, headers, "VLT-2026-0001")
    assert data["route"] == "logistics_tracking"
    assert data["order_facts"]["order_no"] == "VLT-2026-0001"


def test_context_is_structured_and_audit_does_not_store_message(client):
    headers = login_headers(client, "demo_user_01")
    message = "查一下订单 VLT-2026-0001"
    data = chat(client, headers, message)
    with SessionLocal() as db:
        context = db.scalar(select(SessionContextTable))
        assert context.active_order_no == "VLT-2026-0001"
        assert context.pending_intent is None
        assert context.expires_at > context.updated_at
        assert set(SessionContextTable.__table__.columns.keys()) == {
            "shop_id", "user_id", "session_id", "active_order_no",
            "pending_intent", "updated_at", "expires_at",
        }
        audit = db.scalar(select(AuditLogTable).where(AuditLogTable.request_id == data["request_id"]))
        assert json.loads(audit.input_summary) == {"session_id": "memory", "message_length": len(message)}
        assert message not in audit.result_summary
        assert message not in json.dumps(data["tool_trace"], ensure_ascii=False)


def test_pending_intent_is_user_scoped(client):
    alice = login_headers(client, "demo_user_01")
    bob = login_headers(client, "demo_user_04")
    assert_missing(chat(client, alice, "那它什么时候到？"))
    data = chat(client, bob, "VLT-2026-0004")
    assert data["route"] == "order_query"
    assert data["order_facts"]["order_no"] == "VLT-2026-0004"
    assert chat(client, alice, "VLT-2026-0001")["route"] == "logistics_tracking"


def test_model_route_alone_cannot_reuse_order(client, monkeypatch):
    from app.agents.contracts import RouteDecision
    from app.agents.supervisor import SupervisorRouter

    headers = login_headers(client, "demo_user_01")
    chat(client, headers, "查一下订单 VLT-2026-0001")

    async def uncertain_route(self, message):
        return RouteDecision(
            route="order_query", confidence=0.9,
            reason="Test model classification without explicit intent.", source="llm",
        )

    monkeypatch.setattr(SupervisorRouter, "decide", uncertain_route)
    assert_missing(chat(client, headers, "再说说吧"))
