"""Restock pricing tests (fix36): warehouse -> shelf restocks use receipt costs."""
import pytest
from sqlalchemy import select

from infrastructure.db.models import StockBatch
from tests.test_goods_receiving import _figures, _receive, _setup, _summary
from tests.test_shelf_restock import _pull

pytestmark = pytest.mark.asyncio


async def _restock_batches(db, branch_id, product_id):
    db.expire_all()
    rows = await db.execute(
        select(StockBatch)
        .where(
            StockBatch.branch_id == branch_id,
            StockBatch.product_id == product_id,
            StockBatch.batch_number.like("warehouse-%"),
        )
        .order_by(StockBatch.created_at, StockBatch.id)
    )
    return list(rows.scalars().all())


async def test_r1_restock_uses_receipt_cost(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    branch_id, product_id = branch.id, product.id
    resp = await _receive(client, h, branch_id, product_id, 100, 20, line_extra={"unit_cost_kobo": 150})
    assert resp.status_code == 201, resp.text
    pull = await _pull(client, h, branch_id, product_id, 4)
    assert pull.status_code == 201, pull.text
    batches = await _restock_batches(db_session, branch_id, product_id)
    assert len(batches) == 1
    assert batches[0].quantity_received == 4
    assert batches[0].unit_cost_kobo == 150
    assert _figures(await _summary(client, h, branch_id, product_id)) == (100, 76, 24)


async def test_r2_restock_falls_back_to_product_cost(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    branch_id, product_id = branch.id, product.id
    resp = await _receive(client, h, branch_id, product_id, 100, 20)
    assert resp.status_code == 201, resp.text
    pull = await _pull(client, h, branch_id, product_id, 4)
    assert pull.status_code == 201, pull.text
    batches = await _restock_batches(db_session, branch_id, product_id)
    assert len(batches) == 1
    assert batches[0].unit_cost_kobo == 100


async def test_r3_newer_receipt_cost_wins(client, db_session):
    branch, admin, product, h = await _setup(db_session)
    branch_id, product_id = branch.id, product.id
    first = await _receive(client, h, branch_id, product_id, 50, 0, line_extra={"unit_cost_kobo": 150})
    assert first.status_code == 201, first.text
    second = await _receive(client, h, branch_id, product_id, 50, 0, line_extra={"unit_cost_kobo": 200})
    assert second.status_code == 201, second.text
    pull = await _pull(client, h, branch_id, product_id, 4)
    assert pull.status_code == 201, pull.text
    batches = await _restock_batches(db_session, branch_id, product_id)
    assert len(batches) == 1
    assert batches[0].unit_cost_kobo == 200
