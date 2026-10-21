#!/usr/bin/env python3
"""Helper fix for the temporary patcher _patch25b_password_changed_check.py.

Replaces the function _find_path inside its TEST_TEXT (it scanned app.routes,
which finds nothing in this FastAPI) with a fixed map of the paths read from
main.app.openapi(). Changes ONLY that one temporary file in the project root.
No project code is touched.

Usage (project root, project venv):
  python _fix25b_helper.py --tests-only
  python _fix25b_helper.py --dry-run
  python _fix25b_helper.py
  python _fix25b_helper.py --rollback
"""
import argparse
import ast
import datetime
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent
TARGET = ROOT / "_patch25b_password_changed_check.py"
MARKER = "# HELPER-FIXED-PATHS"
BACKUP_PREFIX = "_backup_before_fix25bhelper_"

OLD_LINES = [
    "def _find_path(suffix, method, prefer=None):",
    "    found = []",
    "    for route in app.routes:",
    '        path = getattr(route, "path", "")',
    '        if path.endswith(suffix) and method in (getattr(route, "methods", None) or set()):',
    "            found.append(path)",
    "    if prefer:",
    "        preferred = [p for p in found if prefer in p]",
    "        if len(preferred) == 1:",
    "            return preferred[0]",
    '    assert len(found) == 1, "route lookup for " + suffix + " found " + str(found)',
    "    return found[0]",
]

NEW_LINES = [
    "# HELPER-FIXED-PATHS: this FastAPI hides routes inside _IncludedRouter wrappers,",
    "# so the route list cannot be scanned. These paths were read from main.app.openapi().",
    "_API_PATHS = {",
    '    ("/auth/me", "GET"): "/api/v1/auth/me",',
    '    ("/auth/login", "POST"): "/api/v1/auth/login",',
    '    ("/auth/refresh", "POST"): "/api/v1/auth/refresh",',
    '    ("/auth/reset-password", "POST"): "/api/v1/auth/reset-password",',
    '    ("/change-password", "POST"): "/api/v1/me/change-password",',
    "}",
    "",
    "",
    "def _find_path(suffix, method, prefer=None):",
    "    return _API_PATHS[(suffix, method)]",
]

OLD = "\n".join(OLD_LINES)
NEW = "\n".join(NEW_LINES)

PAIRS = [
    '("/auth/me", "GET")',
    '("/auth/login", "POST")',
    '("/auth/refresh", "POST")',
    '("/auth/reset-password", "POST")',
    '("/change-password", "POST")',
]


def apply_text(text):
    """Return (new_text, status). Raises ValueError on a mismatch."""
    if MARKER in text:
        return text, "already"
    n = text.count(OLD)
    if n != 1:
        raise ValueError("MISMATCH: old helper text found %d times (need exactly 1)" % n)
    return text.replace(OLD, NEW, 1), "changed"


def check_new_file_text(text, old_text):
    """Return a list of problems (empty list = all good)."""
    problems = []
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        problems.append("file is not ASCII")
    if "\r" in text:
        problems.append("file contains CR")
    if text.count(MARKER) != 1:
        problems.append("marker count is not 1")
    expected = len(old_text.split("\n")) + (NEW.count("\n") - OLD.count("\n"))
    if len(text.split("\n")) != expected:
        problems.append("line count is not what the change implies")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        problems.append("patcher no longer parses: %s" % exc)
        return problems
    test_text = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "TEST_TEXT":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        test_text = node.value.value
    if test_text is None:
        problems.append("TEST_TEXT not found")
        return problems
    try:
        compile(test_text, "test_password_changed_at.py", "exec")
    except SyntaxError as exc:
        problems.append("TEST_TEXT does not compile: %s" % exc)
    if "_API_PATHS" not in test_text:
        problems.append("TEST_TEXT has no _API_PATHS")
    if "app.routes" in test_text:
        problems.append("TEST_TEXT still scans app.routes")
    if "\t" in test_text:
        problems.append("TEST_TEXT has a tab")
    return problems


def run_tests():
    results = []

    def check(name, ok):
        results.append(bool(ok))
        print(("PASS " if ok else "FAIL ") + name)

    base = "x = 1\n" + OLD + "\ny = 2\n"
    new, st = apply_text(base)
    check("old text replaced once", st == "changed" and OLD not in new and NEW in new)
    check("rest of text untouched", new.startswith("x = 1\n") and new.endswith("\ny = 2\n"))
    again, st2 = apply_text(new)
    check("second run says already applied", st2 == "already" and again == new)
    try:
        apply_text("x = 1\n")
        check("zero matches is a mismatch", False)
    except ValueError:
        check("zero matches is a mismatch", True)
    try:
        apply_text(OLD + "\n" + OLD + "\n")
        check("two matches is a mismatch", False)
    except ValueError:
        check("two matches is a mismatch", True)
    try:
        ast.parse(NEW)
        ok_parse = True
    except SyntaxError:
        ok_parse = False
    check("new text parses as Python", ok_parse)
    ok_ascii = True
    try:
        NEW.encode("ascii")
        OLD.encode("ascii")
    except UnicodeEncodeError:
        ok_ascii = False
    check("old and new text are ASCII", ok_ascii and "\t" not in NEW and "'''" not in NEW)
    check("new text covers all 5 call pairs", all(p in NEW for p in PAIRS))
    check("old text is the scan version", "app.routes" in OLD and "app.routes" not in NEW)
    ok = all(results)
    print("Self-tests: %d/%d passed" % (sum(results), len(results)))
    return ok


def newest_backup():
    folders = sorted(p for p in ROOT.iterdir() if p.is_dir() and p.name.startswith(BACKUP_PREFIX))
    return folders[-1] if folders else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--tests-only", action="store_true")
    args = ap.parse_args()

    if args.tests_only:
        sys.exit(0 if run_tests() else 1)

    if args.rollback:
        folder = newest_backup()
        if folder is None or not (folder / TARGET.name).is_file():
            print("No backup folder found. Nothing restored.")
            sys.exit(1)
        shutil.copy2(folder / TARGET.name, TARGET)
        print("Restored %s from %s" % (TARGET.name, folder.name))
        sys.exit(0)

    if not TARGET.is_file():
        print("MISMATCH: %s not found" % TARGET)
        sys.exit(1)
    raw = TARGET.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        print("MISMATCH: target has a BOM")
        sys.exit(1)
    try:
        old_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        print("MISMATCH: target is not valid UTF-8")
        sys.exit(1)
    if "\r" in old_text:
        print("MISMATCH: target is not pure LF")
        sys.exit(1)

    try:
        new_text, status = apply_text(old_text)
    except ValueError as exc:
        print(str(exc))
        sys.exit(1)

    if status == "already":
        print("Already applied")
        sys.exit(0)

    problems = check_new_file_text(new_text, old_text)
    if problems:
        for p in problems:
            print("CHECK FAILED (before writing): " + p)
        sys.exit(1)

    print("Change point found exactly once. Lines before: %d, after: %d"
          % (len(old_text.split("\n")), len(new_text.split("\n"))))
    if args.dry_run:
        print("Dry run: nothing written.")
        sys.exit(0)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = ROOT / (BACKUP_PREFIX + stamp)
    folder.mkdir()
    shutil.copy2(TARGET, folder / TARGET.name)
    print("Backup: " + folder.name)

    TARGET.write_bytes(new_text.encode("utf-8"))

    written = TARGET.read_bytes().decode("utf-8")
    after = check_new_file_text(written, old_text)
    if after:
        for p in after:
            print("CHECK FAILED (after writing): " + p)
        shutil.copy2(folder / TARGET.name, TARGET)
        print("Restored the backup automatically.")
        sys.exit(1)
    print("Checks passed: file is ASCII and LF, parses, TEST_TEXT compiles, no app.routes scan.")
    print("Done.")
    sys.exit(0)


if __name__ == "__main__":
    main()
