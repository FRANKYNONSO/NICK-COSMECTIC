"""Refreshes the local read-only catalog/stock cache from the cloud
API. Call this whenever online -- after login, after switching branch,
or via the "Sync Now" button. Wholesale replace-and-reinsert, since
this table is never the source of truth, just a mirror.
"""

from datetime import date, datetime

from sqlalchemy.orm import Session

from api_client import ApiClient
from local_db.models import CachedBranch, CachedCoupon, CachedStockBatch, CachedVariant


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def pull_branches(api: ApiClient, session: Session) -> list[dict]:
    """Fetches branches from the API and refreshes the local mirror.
    Raises on failure (network/auth) -- callers decide whether to fall
    back to `get_cached_branches` when this fails.
    """
    branches = api.list_branches()

    session.query(CachedBranch).delete()
    for branch in branches:
        session.add(CachedBranch(id=branch["id"], name=branch["name"]))
    session.commit()

    return branches


def get_cached_branches(session: Session) -> list[dict]:
    rows = session.query(CachedBranch).order_by(CachedBranch.name).all()
    return [{"id": row.id, "name": row.name} for row in rows]


def pull_catalog(api: ApiClient, session: Session, branch_id: str) -> None:
    products = api.search_products("")

    session.query(CachedStockBatch).delete()
    session.query(CachedVariant).delete()

    for product in products:
        variants = api.get_variants(product["id"])
        for variant in variants:
            session.add(
                CachedVariant(
                    id=variant["id"],
                    product_id=product["id"],
                    product_name=product["name"],
                    variant_name=variant["variant_name"],
                    category=product.get("category") or "Uncategorized",
                    sku=variant["sku"],
                    barcode=variant.get("barcode"),
                    selling_price_kobo=round(variant["selling_price_naira"] * 100),
                    reorder_level=variant["reorder_level"],
                )
            )

    batches = api.get_stock_batches(branch_id)
    for batch in batches:
        if batch["quantity_remaining"] <= 0:
            continue
        expiry = date.fromisoformat(batch["expiry_date"]) if batch.get("expiry_date") else None
        received_at = _parse_iso(batch.get("received_at")) or datetime.utcnow()
        session.add(
            CachedStockBatch(
                id=batch["id"],
                variant_id=batch["variant_id"],
                branch_id=batch["branch_id"],
                quantity_remaining=batch["quantity_remaining"],
                unit_cost_kobo=round(batch["unit_cost_naira"] * 100),
                expiry_date=expiry,
                received_at=received_at,
            )
        )

    session.commit()

def pull_coupons(api: ApiClient, session: Session) -> None:
    """Refreshes the local coupon cache from the cloud's active
    coupons. Call this after login and via "Sync Now" -- same trigger
    points as pull_branches/pull_catalog -- so a coupon issued from
    the web admin portal becomes redeemable at the till without
    needing a full app restart. This is what pos_window.py's
    "This code isn't in the local cache yet. Tap 'Sync Now'..."
    message is telling the cashier to do.

    Unlike pull_catalog, this does NOT delete-and-reinsert wholesale:
    a coupon redeemed locally but not yet pushed to the cloud (see
    OutboxSale sync) would have its local "redeemed" status clobbered
    back to "active" by a naive wholesale replace, making it
    redeemable twice. Instead this only inserts coupons the cache
    doesn't already have, and only overwrites status for coupons the
    cloud reports as no longer active (voided/expired there).
    """
    coupons = api.list_coupons()
    existing_codes = {row.code for row in session.query(CachedCoupon).all()}

    for coupon in coupons:
        if coupon["code"] in existing_codes:
            continue
        expires_at = _parse_iso(coupon["expires_at"]) or datetime.utcnow()
        session.add(
            CachedCoupon(
                id=coupon["id"],
                code=coupon["code"],
                discount_amount_kobo=round(coupon["discount_amount_naira"] * 100),
                status=coupon["status"],
                expires_at=expires_at,
            )
        )
    session.commit()