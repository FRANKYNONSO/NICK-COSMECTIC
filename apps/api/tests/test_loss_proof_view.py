"""Losses History must carry the uploaded proof (photo or video) and the
approval details, so the admin screen can show a View window."""
try:
    from tests.test_loss_approval_revenue import _stocked, _record_loss, _the_adjustment
except ImportError:  # the tests folder is not a package
    from test_loss_approval_revenue import _stocked, _record_loss, _the_adjustment

import pytest

pytestmark = pytest.mark.asyncio

INV = "/api/v1/inventory"


async def test_history_row_carries_proof_and_approval_details(client, db_session):
    branch, product, h = await _stocked(db_session, client)
    resp = await _record_loss(client, h, branch, product)
    assert resp.status_code == 201, resp.text
    adj = await _the_adjustment(db_session, branch)
    proof_url = adj.image_url
    assert proof_url, "the recorded loss should have kept its proof link"

    resp = await client.post(f"{INV}/stock-adjustments/{adj.id}/approve", headers=h)
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"{INV}/losses/history", headers=h, params={"branch_id": str(branch.id)})
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json() if r["id"] == str(adj.id)]
    assert len(rows) == 1, resp.text
    row = rows[0]
    assert row["evidence_url"] == proof_url
    assert row["status"] == "completed"
    assert row["reviewed_at"] is not None
    assert "reviewed_by_name" in row
