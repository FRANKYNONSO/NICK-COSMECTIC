"""Deterministic skincare advisor engine (pure logic, no network, no framework).

The shortlist is built here, never by an LLM. Only products that
 (a) have advisor_enabled=true in advisor_catalog.json,
 (b) are active, priced and currently in stock (combined stock from /storefront/stock),
 (c) fit the budget and the product type the customer asked for
can ever be recommended. Unknown facts stay unknown: nothing is invented.
"""
import json
import re

SKIN_TYPES = ("dry", "oily", "combination", "normal", "sensitive")

# customer word -> words that may appear in product_type
TYPE_WORDS = {
    "lotion": ("lotion",),
    "soap": ("soap",),
    "oil": ("oil",),
    "makeup": ("brow", "pencil", "lip", "foundation", "powder", "makeup"),
    "any": (),
}


def load_catalog_meta(path):
    """Return {product_id: entry} for entries with advisor_enabled true."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    out = {}
    for e in data.get("products", []):
        if e.get("advisor_enabled") is True and e.get("product_id"):
            out[str(e["product_id"])] = e
    return out


def _lower_list(v):
    return [str(x).strip().lower() for x in (v or []) if str(x).strip()]


def _is_baby(entry):
    return "baby" in (entry.get("product_type") or "").lower() or "baby" in (entry.get("name") or "").lower()


def _type_matches(entry, wanted):
    if not wanted or wanted == "any":
        return True
    words = TYPE_WORDS.get(wanted, (wanted,))
    hay = " ".join([(entry.get("product_type") or ""), (entry.get("inventory_category") or "")]).lower()
    return any(w in hay for w in words)


def score_product(entry, answers, weather):
    """Return (score, reasons, has_details). Reasons use ONLY verified facts."""
    score = 0.0
    reasons = []
    skin = (answers.get("skin_type") or "unknown").lower()
    concerns = set(_lower_list(answers.get("concerns")))
    p_skin = _lower_list(entry.get("skin_types"))
    p_conc = _lower_list(entry.get("concerns"))
    has_details = bool(p_skin or p_conc or entry.get("purpose") or entry.get("key_ingredients") or entry.get("spf"))

    ptype = (entry.get("product_type") or "").strip()
    wanted = (answers.get("product_type") or "any").lower()
    if ptype and wanted not in ("any", ""):
        score += 2
        reasons.append("It is a %s, which is what you asked for." % ptype)
    elif ptype:
        score += 0.5

    if skin in SKIN_TYPES and p_skin:
        if skin in p_skin:
            score += 3
            reasons.append("Listed as suitable for %s skin." % skin)
        elif "all" in p_skin:
            score += 1
            reasons.append("Listed as suitable for all skin types.")
        else:
            score -= 3  # verified mismatch: push down
    for c in sorted(concerns & set(p_conc)):
        score += 2
        reasons.append("Listed for %s." % c)

    # weather / sun: only react to facts we hold, never invent benefits
    w = (weather or {}).get("signal")
    if w in ("hot_humid", "hot_dry", "hot") or answers.get("sun_exposure") == "high":
        if entry.get("spf") or "sun protection" in p_conc:
            score += 2
            reasons.append("It is listed with sun protection, useful with strong sun.")
    if w in ("dry", "hot_dry", "cool_dry") and "dryness" in p_conc:
        score += 1
        reasons.append("Listed for dryness, and the weather is dry right now.")
    if w in ("humid", "hot_humid") and "oil control" in p_conc:
        score += 1
        reasons.append("Listed for oil control, and the weather is humid right now.")
    if answers.get("sweating") == "high" and "oil control" in p_conc:
        score += 0.5

    if entry.get("purpose"):
        reasons.append(str(entry["purpose"]).strip())
    if not has_details:
        reasons.append("We do not have more details on file for it yet.")
    elif not reasons:
        reasons.append("It matches what you chose.")
    return score, reasons, has_details


def recommend(answers, products, stock, meta, weather=None, limit=4):
    """products: list of dicts (id, name, price, size, ...) from the storefront catalog.
    stock: {product_id: qty}. meta: from load_catalog_meta.
    Returns dict {items:[...], message:str|None}."""
    wanted = (answers.get("product_type") or "any").lower()
    lo = float(answers.get("budget_min") or 0)
    hi = answers.get("budget_max")
    hi = float(hi) if hi not in (None, "", 0) else None
    wants_baby = "baby care" in _lower_list(answers.get("concerns")) or "baby" in wanted

    cands = []
    for p in products:
        pid = str(p.get("id") or p.get("product_id") or "")
        entry = meta.get(pid)
        if not entry:
            continue
        price = float(p.get("price_naira") or p.get("price") or 0)
        if price <= 0 or (stock.get(pid) or 0) <= 0:
            continue
        if price < lo or (hi is not None and price > hi):
            continue
        merged = dict(entry)
        merged["name"] = p.get("name") or entry.get("name")
        if not _type_matches(merged, wanted):
            continue
        if _is_baby(merged) and not wants_baby:
            continue
        s, reasons, has_details = score_product(merged, answers, weather)
        if s < 0:
            continue
        cands.append({
            "product_id": pid, "name": merged["name"], "price_naira": price,
            "size": p.get("size"), "score": round(s, 2), "reasons": reasons,
            "details_on_file": has_details,
        })
    cands.sort(key=lambda c: (-c["score"], c["price_naira"], c["name"].lower()))
    items = cands[:limit]
    msg = None
    if not items:
        msg = ("We do not have a suitable product for this right now. "
               "You can browse the full shop, or ask us in store.")
    return {"items": items, "message": msg}


def validate_llm_output(raw, shortlist, catalog_names):
    """The LLM may only phrase explanations for shortlisted ids.
    raw: JSON string {"items":[{"product_id":..,"reason":..}]}.
    Returns {product_id: reason} with anything outside the shortlist dropped.
    A reason that names any OTHER catalog product is dropped too."""
    ids = {s["product_id"]: s["name"] for s in shortlist}
    try:
        data = json.loads(raw)
        rows = data.get("items", []) if isinstance(data, dict) else []
    except Exception:
        return {}
    others = [n.lower() for n in catalog_names if n and n not in ids.values() and len(n) >= 4]
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        pid = str(r.get("product_id") or "")
        reason = str(r.get("reason") or "").strip()
        if pid not in ids or not reason or len(reason) > 400:
            continue
        low = reason.lower()
        if any(re.search(r"\b" + re.escape(n) + r"\b", low) for n in others):
            continue
        out[pid] = reason
    return out
