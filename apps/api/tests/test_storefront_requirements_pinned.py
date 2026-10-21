# Tests for patch 18a: the storefront requirements are pinned.
# Plain sync tests. They only read the text file. They never import the shop.
import os

REQ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "ecommerce", "requirements.txt")

EXPECTED = {
    "fastapi": "0.142.2",
    "starlette": "1.7.0",
    "pydantic": "2.13.5",
    "uvicorn": "0.54.0",
    "httpx": "0.28.1",
    "jinja2": "3.1.6",
    "itsdangerous": "2.2.0",
    "python-multipart": "0.0.32",
}


def _read_bytes():
    with open(REQ, "rb") as fh:
        return fh.read()


def _requirements():
    rows = []
    for line in _read_bytes().decode("utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            rows.append(text)
    return rows


def _name_and_version(row):
    name, _, version = row.partition("==")
    name = name.split("[", 1)[0].strip().lower().replace("_", "-")
    return name, version.strip()


def test_every_requirement_uses_exact_pin():
    rows = _requirements()
    assert rows
    for row in rows:
        assert "==" in row, "not pinned: " + row


def test_no_open_ended_rules_left():
    for row in _requirements():
        assert ">=" not in row and "~=" not in row and ">" not in row, "open rule: " + row


def test_all_needed_packages_are_listed():
    names = set(_name_and_version(r)[0] for r in _requirements())
    for needed in EXPECTED:
        assert needed in names, "missing: " + needed


def test_versions_are_the_ones_that_run_today():
    found = dict(_name_and_version(r) for r in _requirements())
    for name, version in EXPECTED.items():
        assert found.get(name) == version, name + " should be " + version


def test_file_is_lf_only_without_bom():
    raw = _read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
