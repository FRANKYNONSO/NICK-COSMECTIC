"""Adds bags fields to api/v1/schemas/warehouse.py (idempotent).
Run from anywhere:  python patch_warehouse_schema.py <path-to-schemas\\warehouse.py>
"""
import sys

path = sys.argv[1]
raw = open(path, "rb").read()
bom = raw.startswith(b"\xef\xbb\xbf")
text = raw.decode("utf-8-sig")
crlf = "\r\n" in text
s = text.replace("\r\n", "\n")

if "bags_count" in s:
    print("Already patched - nothing to do.")
    sys.exit(0)

def sub(old, new):
    global s
    n = s.count(old)
    if n != 1:
        sys.exit(f"ABORTED: expected 1 match, found {n} for:\n{old}")
    s = s.replace(old, new)

# a) StockTransferOut
sub("    dozens_half: bool = False\n    pieces_count: int | None = None\n",
    "    dozens_half: bool = False\n"
    "    bags_count: int | None = None\n"
    "    bags_pieces_per_unit: int | None = None\n"
    "    bags_half: bool = False\n"
    "    pieces_count: int | None = None\n")

# b) PushToWarehouseCreate
sub("    dozens_half: bool = False\n    pieces_count: int | None = Field(default=None, ge=0)\n",
    "    dozens_half: bool = False\n"
    "    bags_count: int | None = Field(default=None, ge=0)\n"
    "    bags_pieces_per_unit: int | None = Field(default=None, gt=0)\n"
    "    bags_half: bool = False\n"
    "    pieces_count: int | None = Field(default=None, ge=0)\n")

# c) PushableInvoiceLineItemOut
sub("    dozens_remaining_halves: int = 0\n    pieces_total: int = 0\n",
    "    dozens_remaining_halves: int = 0\n"
    "    bags_total_halves: int = 0\n"
    "    bags_remaining_halves: int = 0\n"
    "    pieces_total: int = 0\n")

out = s.replace("\n", "\r\n") if crlf else s
data = out.encode("utf-8")
if bom:
    data = b"\xef\xbb\xbf" + data
open(path, "wb").write(data)
print("Patched OK: 3 places.")
