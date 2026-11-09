# P3c: the top bar shows "Store assistant" for the store_assistant role.
# TEXT CHECKS ONLY. These tests read the JS source files. They do not run a browser.
from pathlib import Path

JS = Path(__file__).resolve().parents[3] / "apps" / "web_admin" / "js"
FILES = [
    "category-variants.js", "inventory.js", "invoice.js", "warehouse.js",
    "customer-requests.js", "dashboard.js", "low-stock.js", "manage-units.js",
    "pos.js", "receive-goods.js", "records.js", "reports.js", "restock-list.js",
    "sales.js", "settings.js",
]
NEW_LINE = "staff: 'Cashier', store_assistant: 'Store assistant' }; // PATCHP3C-ROLE-LABEL"
OLD_LINE = "staff: 'Cashier' };"


def _src(name):
    return (JS / name).read_bytes().decode("utf-8")


def test_a_all_fifteen_files_exist():
    assert len(FILES) == 15
    for name in FILES:
        assert (JS / name).exists(), name


def test_b_every_file_has_the_store_assistant_label():
    for name in FILES:
        assert NEW_LINE in _src(name), name


def test_c_every_file_has_one_role_labels_definition():
    for name in FILES:
        assert _src(name).count("const roleLabels = {") == 1, name


def test_d_old_line_without_store_assistant_is_gone():
    for name in FILES:
        assert OLD_LINE not in _src(name), name


def test_e_every_file_carries_the_marker():
    for name in FILES:
        assert "PATCHP3C-ROLE-LABEL" in _src(name), name
