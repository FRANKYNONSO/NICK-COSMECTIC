"""Warehouse row creation test (fix37)."""
import pytest
from sqlalchemy import func, select

from api.v1.routers.warehouse import _get_or_create_warehouse_row
from infrastructure.db.models import WarehouseStock
from tests.test_goods_receiving import _setup

pytestmark = pytest.mark.asyncio


async def test_w1_get_or_create_makes_exactly_one_row(db_session):
    branch, admin, product, h = await _setup(db_session)
    bid, pid = branch.id, product.id

    first = await _get_or_create_warehouse_row(db_session, bid, pid)
    assert first.quantity_pieces == 0
    first.quantity_pieces = 7
    await db_session.flush()

    second = await _get_or_create_warehouse_row(db_session, bid, pid)
    assert second.quantity_pieces == 7

    count = await db_session.scalar(
        select(func.count())
        .select_from(WarehouseStock)
        .where(WarehouseStock.branch_id == bid, WarehouseStock.product_id == pid)
    )
    assert count == 1
