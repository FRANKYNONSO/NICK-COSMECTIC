# PATCHP3M-TESTS: every permission key used in a route guard exists in the registry
import re
from pathlib import Path

from nickcosmetics_core.domain.users.permission_registry import REGISTRY_KEYS

API_DIR = Path(__file__).resolve().parents[1] / "api"
SKIP_PARTS = ("_backup", "__pycache__", "_archive", "_hold", ".bak")

_CALL = re.compile(r"\brequire_permission\(([^)]*)\)")
_ANY_CALL = re.compile(r"\brequire_any_permission\(([^)]*)\)")
_CONST = re.compile(r'^(_[A-Z_]*PERMISSION)\s*=\s*"([a-z][a-z0-9_]*)"', re.MULTILINE)
_QUOTED = re.compile(r"[\"']([a-z][a-z0-9_]*)[\"']")


def scan_text(text):
    """Return (keys, problems) for one source text."""
    consts = {m.group(1): m.group(2) for m in _CONST.finditer(text)}
    keys = set()
    problems = []

    def add(piece):
        quoted = _QUOTED.fullmatch(piece)
        if quoted:
            keys.add(quoted.group(1))
        elif piece in consts:
            keys.add(consts[piece])
        else:
            problems.append(piece)

    for m in _CALL.finditer(text):
        arg = m.group(1).strip()
        if not arg or ":" in arg:
            continue  # empty call in a comment, or the def line itself
        add(arg)
    for m in _ANY_CALL.finditer(text):
        arg = m.group(1).strip()
        if not arg or ":" in arg:
            continue
        for piece in arg.split(","):
            piece = piece.strip()
            if piece:
                add(piece)
    return keys, problems


def _api_files():
    files = []
    for path in sorted(API_DIR.rglob("*.py")):
        if any(part in str(path) for part in SKIP_PARTS):
            continue
        files.append(path)
    return files


def _scan_all():
    per_file = {}
    problems = []
    for path in _api_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        keys, probs = scan_text(text)
        if keys:
            per_file[path.name] = keys
        problems.extend("%s: %s" % (path.name, p) for p in probs)
    return per_file, problems


def test_api_folder_is_found_and_not_empty():
    assert API_DIR.is_dir(), str(API_DIR)
    assert len(_api_files()) >= 10


def test_every_guard_key_is_in_the_registry():
    per_file, problems = _scan_all()
    assert not problems, "guard calls in an unknown form: " + "; ".join(problems)
    unknown = sorted(
        "%s uses '%s'" % (name, key)
        for name, keys in per_file.items()
        for key in keys
        if key not in REGISTRY_KEYS
    )
    assert not unknown, "keys that are not in the registry: " + "; ".join(unknown)


def test_scanner_finds_known_keys_including_constant_and_any_forms():
    per_file, _ = _scan_all()
    found = set()
    for keys in per_file.values():
        found |= keys
    for key in ("manage_inventory", "process_sale", "manage_suppliers", "view_purchase_invoices", "use_warehouse"):
        assert key in found, key


def test_scanner_catches_a_typo_key():
    keys, problems = scan_text('x = Depends(require_permission("manage_user"))')
    assert keys == {"manage_user"}
    assert not problems
    assert "manage_user" not in REGISTRY_KEYS


def test_scanner_flags_an_unknown_form():
    keys, problems = scan_text("x = Depends(require_permission(some_var))")
    assert not keys
    assert problems == ["some_var"]


def test_scanner_reads_any_permission_keys():
    keys, problems = scan_text('x = Depends(require_any_permission("manage_inventory", "bad_key"))')
    assert keys == {"manage_inventory", "bad_key"}
    assert not problems


def test_scanner_reads_a_permission_constant():
    text = '_PERMISSION = "manage_suppliers"\nx = Depends(require_permission(_PERMISSION))\n'
    keys, problems = scan_text(text)
    assert keys == {"manage_suppliers"}
    assert not problems