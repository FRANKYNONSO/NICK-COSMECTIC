from datetime import datetime, timedelta

import pytest

from nickcosmetics_core.application.sales_service import CheckoutLine, SalesService
from nickcosmetics_core.domain.inventory.stock import StockBatch
from nickcosmetics_core.domain.money import Money
from nickcosmetics_core.domain.sales.sale import SaleChannel
from nickcosmetics_core.domain.users.user import Role, User
from nickcosmetics_core.exceptions import InsufficientStockError
from tests.fakes import FakeSaleRepository, FakeStockBatchRepository


def _make_batches():
    now = datetime.utcnow()
    return [
        StockBatch(
            id="batch-old",
            variant_id="variant-1",
            branch_id="branch-1",
            quantity_received=10,
            quantity_remaining=3,
            unit_cost=Money.from_naira(500),
            received_at=now - timedelta(days=10),
        ),
        StockBatch(
            id="batch-new",
            variant_id="variant-1",
            branch_id="branch-1",
            quantity_received=10,
            quantity_remaining=10,
            unit_cost=Money.from_naira(520),
            received_at=now - timedelta(days=1),
        ),
    ]


def test_checkout_draws_from_oldest_batch_first():
    stock_repo = FakeStockBatchRepository(_make_batches())
    sale_repo = FakeSaleRepository()
    service = SalesService(stock_repo, sale_repo)

    sale = service.checkout(
        sale_id="sale-1",
        branch_id="branch-1",
        served_by="user-1",
        channel=SaleChannel.POS,
        local_ref_id="local-ref-001",
        lines=[
            CheckoutLine(
                variant_id="variant-1", quantity=5, unit_price=Money.from_naira(1500)
            )
        ],
    )

    # 3 from the old batch, 2 from the new batch (FIFO)
    assert sale.items[0].stock_batch_id == "batch-old"
    assert sale.items[0].quantity == 3
    assert sale.items[1].stock_batch_id == "batch-new"
    assert sale.items[1].quantity == 2
    assert sale.total == Money.from_naira(1500 * 5)


def test_checkout_is_idempotent_on_local_ref_id():
    stock_repo = FakeStockBatchRepository(_make_batches())
    sale_repo = FakeSaleRepository()
    service = SalesService(stock_repo, sale_repo)

    line = [CheckoutLine("variant-1", 5, Money.from_naira(1500))]
    first = service.checkout(
        sale_id="sale-1",
        branch_id="branch-1",
        served_by="user-1",
        channel=SaleChannel.POS,
        local_ref_id="local-ref-001",
        lines=line,
    )
    # Simulate a retried sync push with the same local_ref_id
    second = service.checkout(
        sale_id="sale-1-retry-attempt",
        branch_id="branch-1",
        served_by="user-1",
        channel=SaleChannel.POS,
        local_ref_id="local-ref-001",
        lines=line,
    )

    assert first is second
    # Stock should only have been deducted once
    remaining = sum(
        b.quantity_remaining
        for b in stock_repo.get_batches_for_variant("variant-1", "branch-1")
    )
    assert remaining == 13 - 5  # 3 + 10 initial, minus 5 sold once


def test_checkout_raises_when_stock_insufficient():
    stock_repo = FakeStockBatchRepository(_make_batches())
    sale_repo = FakeSaleRepository()
    service = SalesService(stock_repo, sale_repo)

    with pytest.raises(InsufficientStockError):
        service.checkout(
            sale_id="sale-1",
            branch_id="branch-1",
            served_by="user-1",
            channel=SaleChannel.POS,
            local_ref_id="local-ref-002",
            lines=[CheckoutLine("variant-1", 999, Money.from_naira(1500))],
        )


def test_staff_role_permissions():
    staff = User(id="u1", full_name="Jane Staff", role=Role.STAFF)
    manager = User(id="u2", full_name="Sam Manager", role=Role.BRANCH_MANAGER)

    assert staff.can("process_sale") is True
    assert staff.can("manage_inventory") is False
    assert manager.can("manage_inventory") is True
    assert manager.can("remove_super_admin") is False

    assert staff.can_view_sales_of("u1") is True
    assert staff.can_view_sales_of("someone-else") is False
    assert manager.can_view_sales_of("someone-else") is True
