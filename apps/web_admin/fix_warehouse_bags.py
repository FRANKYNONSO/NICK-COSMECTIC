#!/usr/bin/env python3
"""
Fixes the "Cannot read properties of null (reading 'style')" error in
warehouse.js (openWhQtyDetailPopup) by adding the missing Bags block to the
quantity popup in warehouse.html. Also repairs the garbled characters
(mojibake) caused by the file being saved in the wrong encoding.

Usage:
    python fix_warehouse_bags.py                 # patches ./warehouse.html
    python fix_warehouse_bags.py path/to/warehouse.html

A backup is written next to the file as warehouse.html.bak before changes.
Safe to run more than once.
"""
import re
import shutil
import sys
from pathlib import Path

BAGS_BLOCK = '''<div class="field" id="wh-qd-bags-block" style="border:1px solid #ddd;border-radius:8px;padding:10px;margin-bottom:10px;">
            <label>Bags</label>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
              <div>
                <small>How many bags</small>
                <input type="number" id="wh-qd-bags-count" min="0" step="1" value="0" style="width:100%;" />
              </div>
              <div>
                <small>Pieces per bag</small>
                <input type="number" id="wh-qd-bags-ppu" min="1" step="1" style="width:100%;" />
              </div>
            </div>
            <label style="display:flex;align-items:center;gap:8px;margin-top:8px;font-weight:normal;">
              <input type="checkbox" id="wh-qd-bags-half" /> plus &#189; bag
            </label>
          </div>

          '''

# Common mojibake sequences seen in this file -> the intended characters.
MOJIBAKE = {
    "\u00e2\u20ac\u201d": "\u2014",  # â€”  -> em dash
    "\u00e2\u20ac\u00a6": "\u2026",  # â€¦  -> ellipsis
    "\u00e2\u20ac\u2122": "\u2019",  # â€™  -> right single quote
    "\u00e2\u20ac\u02dc": "\u2018",  # â€˜  -> left single quote
    "\u00e2\u20ac\u0153": "\u201c",  # â€œ  -> left double quote
    "\u00e2\u20ac\u009d": "\u201d",  # â€\x9d -> right double quote
    "\u00e2\u20ac\u201c": "\u2013",  # â€“  -> en dash
}


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "warehouse.html")
    if not path.exists():
        sys.exit(f"File not found: {path}")

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")

    original = text
    changes = []

    # 1) Add the missing Bags block (before the Pieces field in the popup).
    if "wh-qd-bags-block" in text:
        changes.append("Bags block already present - skipped")
    else:
        pattern = re.compile(
            r'<div class="field">\s*<label for="wh-qd-pieces-count">'
        )
        match = pattern.search(text)
        if not match:
            sys.exit(
                "Could not find the Pieces field in the popup "
                "(label for=\"wh-qd-pieces-count\"). No changes made."
            )
        text = text[: match.start()] + BAGS_BLOCK + text[match.start():]
        changes.append("Added Bags block to the quantity popup")

    # 2) Repair garbled characters.
    fixed = 0
    for bad, good in MOJIBAKE.items():
        count = text.count(bad)
        if count:
            text = text.replace(bad, good)
            fixed += count
    if fixed:
        changes.append(f"Repaired {fixed} garbled character sequence(s)")

    if text == original:
        print("Nothing to change.")
        return

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copyfile(path, backup)
    path.write_text(text, encoding="utf-8", newline="")

    print(f"Patched {path} (backup: {backup})")
    for c in changes:
        print(f" - {c}")
    print("Now hard-refresh the page with Ctrl+Shift+R (service worker cache).")


if __name__ == "__main__":
    main()
