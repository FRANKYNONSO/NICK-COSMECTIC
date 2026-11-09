"""PATCHP1B-SEED-PERMISSIONS: checks for the permission seeding migration.

No database is used here. The migration itself is run by hand on shelf_test first.
"""
import ast
import importlib.util
import pathlib

from nickcosmetics_core.domain.users.user import ROLE_PERMISSION_MATRIX

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSIONS / "permseed01_seed_role_permissions.py"


def _load():
    spec = importlib.util.spec_from_file_location("permseed01_mod", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _heads():
    revs, downs = set(), set()
    for path in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(path.read_bytes())
        for node in tree.body:
            name = value = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                name, value = node.target.id, node.value
            if name == "revision":
                revs.add(ast.literal_eval(value))
            elif name == "down_revision":
                v = ast.literal_eval(value)
                if v is None:
                    continue
                if isinstance(v, (tuple, list)):
                    downs.update(v)
                else:
                    downs.add(v)
    return revs - downs


def test_frozen_lists_equal_the_matrix():
    mod = _load()
    # old frozen seed migration uses key 'staff'; the matrix now uses 'cashier'
    frozen = {("cashier" if r == "staff" else r): n
              for r, n in mod._ROLE_PERMISSIONS.items()}
    assert set(frozen) == set(ROLE_PERMISSION_MATRIX)
    for role, names in frozen.items():
        assert set(names) == set(ROLE_PERMISSION_MATRIX[role]), role


def test_no_duplicate_names_in_frozen_lists():
    mod = _load()
    for role, names in mod._ROLE_PERMISSIONS.items():
        assert len(names) == len(set(names)), role


def test_revision_chain():
    mod = _load()
    assert mod.revision == "permseed01"
    assert mod.down_revision == "stasst01"


def test_single_migration_head():
    assert len(_heads()) == 1  # PATCHHEAD-TEST: exactly one head; never hard-code its name


def test_upgrade_only_adds(monkeypatch):
    mod = _load()
    sent = []
    monkeypatch.setattr(mod.op, "execute", lambda sql: sent.append(str(sql)))
    mod.upgrade()
    assert len(sent) == 4
    for sql in sent:
        assert sql.startswith("UPDATE roles SET permissions")
        assert "NOT (permissions @>" in sql
        upper = sql.upper()
        assert "DELETE" not in upper
        assert "DROP" not in upper
        assert "TRUNCATE" not in upper


def test_downgrade_removes_nothing(monkeypatch):
    mod = _load()
    sent = []
    monkeypatch.setattr(mod.op, "execute", lambda sql: sent.append(str(sql)))
    mod.downgrade()
    assert sent == []
