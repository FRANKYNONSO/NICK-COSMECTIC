"""P2c-2b static test: the Restock window removes the cost and price
boxes for roles without manage_products. Text check only. It does not
prove the browser result."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, "..", "..", "web_admin", "js", "low-stock.js")


def test_a_boxes_removed_without_manage_products():
    with open(JS, encoding="utf-8") as f:
        t = f.read()
    assert "PATCHP2C2B-RESTOCK-BOXES" in t
    assert "Perms.can('manage_products')" in t
    assert "document.getElementById('restock-cost').value" not in t
    assert "document.getElementById('restock-price').value" not in t
