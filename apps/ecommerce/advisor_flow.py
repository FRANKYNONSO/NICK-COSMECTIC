"""Beauty advisor chat flow logic (pure Python: no network, no web framework).

What lives here
- categories built live from the shop's own catalog (enabled + listed + in stock)
- a free, rules-based reader for typed messages (works with no AI at all)
- the question flow per kind of category (skincare / fragrance / other)
- the safety text and the red-flag check (we never diagnose)
- small in-memory limiters (per visitor, and a shop-wide AI daily cap)

The shortlist of products is ALWAYS made by advisor_engine. Nothing in this file
(and nothing the AI says) can add a product that the engine did not pick.
"""
import re
import time
import datetime

import advisor_engine as engine

SKIN_TYPES = ("dry", "oily", "combination", "normal", "sensitive")
CONCERNS = (
    "dryness", "oil control", "dark spots", "acne", "sun protection",
    "brightening", "sensitive skin", "baby care",
)
SCENTS = ("sweet", "fresh", "fruity", "floral", "woody", "musky", "vanilla", "strong", "light")
OCCASIONS = ("everyday", "office", "date night", "party", "special occasion")

DISCLAIMER = (
    "These recommendations are for cosmetic and skincare guidance only and are not a "
    "medical diagnosis. If you have a persistent, painful, severe or worsening skin "
    "condition, please consult a qualified healthcare professional or dermatologist."
)
MEDICAL_REPLY = (
    "I can't diagnose skin conditions, and what you describe may need a professional. "
    "Please see a doctor, pharmacist or dermatologist about it. If you like, I can still "
    "suggest gentle everyday products from our shop."
)

_SKIN_KIND_WORDS = (
    "lotion", "cream", "oil", "soap", "serum", "cleanser", "toner", "moistur", "sunscreen",
    "scrub", "mask", "wash", "balm", "butter", "face", "body", "skin", "exfoliat",
)
_FRAGRANCE_WORDS = ("perfume", "fragrance", "spray", "mist", "cologne", "parfum")

_MEDICAL_WORDS = (
    "eczema", "infection", "infected", "rash", "fungal", "fungus", "ringworm", "pus",
    "bleeding", "cancer", "mole", "swollen", "swelling", "shingles", "psoriasis",
    "burning", "blister", "open wound", "lump", "painful", "keeps getting worse",
)

# ---------------------------------------------------------------- categories


def category_label(entry):
    """Customer-facing category name for one catalog entry. It comes from the
    inventory category when the shop has one, else from the product type."""
    raw = (entry.get("inventory_category") or entry.get("product_type") or "").strip()
    return raw[:1].upper() + raw[1:] if raw else ""


def category_slug(label):
    return re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")


def category_kind(label):
    low = (label or "").lower()
    if any(w in low for w in _FRAGRANCE_WORDS):
        return "fragrance"
    if any(w in low for w in _SKIN_KIND_WORDS):
        return "skin"
    return "other"


def available_categories(products, stock, meta):
    """[{slug,label,kind,count}] for categories that have at least one enabled,
    listed, in-stock product right now. Nothing is hard-coded."""
    seen = {}
    for p in products:
        pid = str(p.get("id") or p.get("product_id") or "")
        entry = meta.get(pid)
        if not entry:
            continue
        try:
            price = float(p.get("price_naira") or p.get("price") or 0)
        except (TypeError, ValueError):
            price = 0
        if price <= 0 or (stock.get(pid) or 0) <= 0:
            continue
        label = category_label(entry)
        slug = category_slug(label)
        if not slug:
            continue
        row = seen.setdefault(slug, {"slug": slug, "label": label, "kind": category_kind(label), "count": 0})
        row["count"] += 1
    return sorted(seen.values(), key=lambda r: (-r["count"], r["label"].lower()))


def meta_for_category(meta, slug):
    return {pid: e for pid, e in meta.items() if category_slug(category_label(e)) == slug}


# ---------------------------------------------------------------- state
FIELDS = {
    "skin": ("skin_type", "concerns", "budget"),
    "fragrance": ("scent", "occasion", "budget"),
    "other": ("budget",),
}


def new_state():
    return {"category": None, "skin_type": None, "concerns": None, "scent": None,
            "occasion": None, "budget_min": None, "budget_max": None, "done": []}


def clean_state(raw, valid_slugs):
    """The session is ours, but still treat it as untrusted on read."""
    s = new_state()
    if not isinstance(raw, dict):
        return s
    if raw.get("category") in valid_slugs:
        s["category"] = raw["category"]
    if raw.get("skin_type") in SKIN_TYPES + ("unknown",):
        s["skin_type"] = raw["skin_type"]
    if isinstance(raw.get("concerns"), list):
        s["concerns"] = [c for c in raw["concerns"] if c in CONCERNS][:8]
    if isinstance(raw.get("scent"), list):
        s["scent"] = [c for c in raw["scent"] if c in SCENTS][:4]
    if raw.get("occasion") in OCCASIONS:
        s["occasion"] = raw["occasion"]
    for k in ("budget_min", "budget_max"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and 0 <= v <= 10_000_000:
            s[k] = float(v)
    if isinstance(raw.get("done"), list):
        s["done"] = [d for d in raw["done"] if d in ("skin_type", "concerns", "scent", "occasion", "budget")][:5]
    return s


def next_field(state, kind):
    for f in FIELDS.get(kind, ("budget",)):
        if f not in state["done"]:
            return f
    return None


# ---------------------------------------------------------------- budget
BUDGET_CHIPS = [
    ("Any budget", "any"),
    ("Under \u20A63,000", "under_3000"),
    ("\u20A63,000 to \u20A68,000", "3000_8000"),
    ("\u20A68,000 to \u20A620,000", "8000_20000"),
    ("Over \u20A620,000", "over_20000"),
]
BUDGET_RANGES = {
    "any": (None, None), "under_3000": (0, 3000), "3000_8000": (3000, 8000),
    "8000_20000": (8000, 20000), "over_20000": (20000, None),
}

_NUM = r"(?:\u20A6|n|ngn)?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m)?"


def _to_naira(num, suffix):
    v = float(str(num).replace(",", ""))
    if suffix == "k":
        v *= 1000
    elif suffix == "m":
        v *= 1_000_000
    return v


def parse_budget(text):
    """Read 'under 5000', 'below 3k', '3000 to 8000', 'around 10,000' from free
    text. Returns (min, max) or None. Only clear money phrases count."""
    t = (text or "").lower().replace("\u20a6", " n ")
    m = re.search(r"(?:between\s+)?" + _NUM + r"\s*(?:to|-|and)\s*" + _NUM, t)
    if m:
        a = _to_naira(m.group(1), m.group(2) or m.group(4))
        b = _to_naira(m.group(3), m.group(4))
        lo, hi = sorted((a, b))
        if hi >= 100:
            return (lo, hi)
    m = re.search(r"(?:under|below|less than|max(?:imum)?|within|not more than|up to|at most)\s*" + _NUM, t)
    if m:
        v = _to_naira(m.group(1), m.group(2))
        if v >= 100:
            return (0, v)
    m = re.search(r"(?:over|above|more than|at least|from)\s*" + _NUM, t)
    if m:
        v = _to_naira(m.group(1), m.group(2))
        if v >= 100:
            return (v, None)
    m = re.search(r"(?:budget|around|about|roughly)\s*(?:is|of)?\s*" + _NUM, t)
    if m:
        v = _to_naira(m.group(1), m.group(2))
        if v >= 100:
            return (0, v)
    return None


# ---------------------------------------------------------------- free text reader
_SKIN_PHRASES = {
    "dry": ("dry skin", "my skin is dry", "dry face", "flaky", "tight skin"),
    "oily": ("oily", "greasy", "shiny face", "shiny skin"),
    "combination": ("combination",),
    "normal": ("normal skin",),
    "sensitive": ("sensitive",),
}
_CONCERN_PHRASES = {
    "dark spots": ("dark spot", "dark mark", "hyperpigment", "pigmentation", "discolor", "discolour"),
    "acne": ("acne", "pimple", "breakout", "break out", "blemish"),
    "dryness": ("dry", "flaky", "dryness"),
    "oil control": ("oily", "oil control", "greasy", "shiny"),
    "sun protection": ("sunscreen", "sun protection", "spf", "sun block", "sunblock"),
    "brightening": ("brighten", "glow", "even tone", "uneven", "dull", "radiant", "bright"),
    "sensitive skin": ("sensitive", "irritat", "redness", "reddish"),
    "baby care": ("baby", "infant", "newborn", "toddler"),
}
_SCENT_PHRASES = {s: (s,) for s in SCENTS}
_SCENT_PHRASES["fruity"] = ("fruity", "fruit")
_OCC_PHRASES = {
    "everyday": ("everyday", "every day", "daily", "casual"),
    "office": ("office", "work"),
    "date night": ("date night", "date"),
    "party": ("party", "club", "night out"),
    "special occasion": ("special occasion", "wedding", "event", "formal"),
}
_SHOW_WORDS = ("show me", "recommend", "just show", "what do you have", "what do you suggest", "suggest")


def _has(low, phrase):
    """Phrase starts at a word boundary, so 'dry' does not fire inside 'laundry'."""
    return re.search(r"\b" + re.escape(phrase), low) is not None


def has_medical_flag(text):
    low = (text or "").lower()
    return any(w in low for w in _MEDICAL_WORDS)


def read_text(text, categories):
    """Rules-based understanding of a typed message. Returns a dict with only
    what was clearly said. This is the FREE path; the AI is only a helper for
    messages this cannot read."""
    low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    out = {}
    for c in categories:
        words = {c["label"].lower(), c["label"].lower().rstrip("s")}
        if any(re.search(r"\b" + re.escape(w) + r"s?\b", low) for w in words if w):
            out["category"] = c["slug"]
            break
    if "category" not in out:
        # friendly synonyms -> whatever category name contains the word
        syn = {"perfume": _FRAGRANCE_WORDS, "body spray": ("spray", "mist"), "cream": ("cream",),
               "moisturizer": ("lotion", "cream", "moistur"), "moisturiser": ("lotion", "cream", "moistur")}
        for word, needles in syn.items():
            if word in low:
                for c in categories:
                    if any(n in c["label"].lower() for n in needles):
                        out["category"] = c["slug"]
                        break
            if "category" in out:
                break
    for skin, phrases in _SKIN_PHRASES.items():
        if any(_has(low, p) for p in phrases):
            out["skin_type"] = skin
            break
    conc = [k for k, ph in _CONCERN_PHRASES.items() if any(_has(low, p) for p in ph)]
    if conc:
        out["concerns"] = conc
    sc = [k for k, ph in _SCENT_PHRASES.items() if any(re.search(r"\b" + re.escape(p) + r"\b", low) for p in ph)]
    if sc:
        out["scent"] = sc[:4]
    for occ, ph in _OCC_PHRASES.items():
        if any(re.search(r"\b" + re.escape(p) + r"\b", low) for p in ph):
            out["occasion"] = occ
            break
    b = parse_budget(text)
    if b:
        out["budget"] = b
    if any(w in low for w in _SHOW_WORDS):
        out["show_now"] = True
    if has_medical_flag(text):
        out["medical"] = True
    return out


def apply_reading(state, reading, kind_of=None):
    """Fold a reading (from rules or from the AI, already validated) into the
    state. Skin, scent and budget facts are kept even before a category is
    chosen, so nothing the customer said is lost. Returns True if anything changed."""
    changed = False
    cat = reading.get("category")
    if cat and cat != state["category"]:
        state.update(new_state())
        state["category"] = cat
        changed = True
    sk = reading.get("skin_type")
    if sk in SKIN_TYPES + ("unknown",):
        state["skin_type"] = sk
        _mark(state, "skin_type")
        changed = True
    cs = [c for c in (reading.get("concerns") or []) if c in CONCERNS]
    if cs:
        state["concerns"] = cs
        _mark(state, "concerns")
        changed = True
    sc = [c for c in (reading.get("scent") or []) if c in SCENTS]
    if sc:
        state["scent"] = sc
        _mark(state, "scent")
        changed = True
    oc = reading.get("occasion")
    if oc in OCCASIONS:
        state["occasion"] = oc
        _mark(state, "occasion")
        changed = True
    b = reading.get("budget")
    if b:
        state["budget_min"], state["budget_max"] = b[0], b[1]
        _mark(state, "budget")
        changed = True
    return changed


def _mark(state, field):
    if field not in state["done"]:
        state["done"].append(field)


# ---------------------------------------------------------------- engine bridge
def engine_answers(state):
    return {
        "skin_type": state.get("skin_type") or "unknown",
        "concerns": state.get("concerns") or [],
        "product_type": "any",
        "budget_min": state.get("budget_min") or 0,
        "budget_max": state.get("budget_max"),
        "sun_exposure": "unknown",
        "sweating": "unknown",
    }


def pick_products(state, products, stock, meta, limit=4):
    """Engine shortlist for the chosen category. Returns the engine result dict."""
    slug = state.get("category")
    sub = meta_for_category(meta, slug)
    res = engine.recommend(engine_answers(state), products, stock, sub, None, limit=50)
    items = res["items"]
    kind = category_kind(next((category_label(e) for e in sub.values()), ""))
    if kind == "fragrance":
        items = _rerank_fragrance(items, sub, state)
    res["items"] = items[:limit]
    return res


def _rerank_fragrance(items, sub, state):
    want_scent = set(state.get("scent") or [])
    want_occ = state.get("occasion")
    out = []
    for it in items:
        e = sub.get(it["product_id"], {})
        bonus = 0.0
        have = [str(x).lower() for x in (e.get("scent_family") or [])]
        hit = sorted(want_scent & set(have))
        if hit:
            bonus += 2 * len(hit)
            it["reasons"] = ["Listed as %s." % ", ".join(hit)] + it["reasons"]
        occs = [str(x).lower() for x in (e.get("occasions") or [])]
        if want_occ and want_occ in occs:
            bonus += 2
            it["reasons"] = ["Listed for %s use." % want_occ] + it["reasons"]
        it["score"] = round(it["score"] + bonus, 2)
        out.append(it)
    out.sort(key=lambda c: (-c["score"], c["price_naira"], c["name"].lower()))
    return out


# ---------------------------------------------------------------- questions
def question_for(field, state):
    """(text, chips-spec) for the next question. chips-spec: dict or None."""
    if field == "skin_type":
        opts = [("Dry", "dry"), ("Oily", "oily"), ("Combination", "combination"),
                ("Normal", "normal"), ("Sensitive", "sensitive"), ("Not sure", "unknown")]
        return ("What is your skin type? If you are not sure, that is fine.",
                {"field": "skin_type", "multi": False, "options": opts})
    if field == "concerns":
        opts = [("Dark spots", "dark spots"), ("Acne", "acne"), ("Dryness", "dryness"),
                ("Oil control", "oil control"), ("Uneven tone or dullness", "brightening"),
                ("Sun protection", "sun protection"), ("Sensitive skin", "sensitive skin"),
                ("Baby care", "baby care")]
        return ("What would you like help with? Pick all that apply.",
                {"field": "concerns", "multi": True, "options": opts})
    if field == "scent":
        opts = [(s.capitalize(), s) for s in SCENTS]
        return ("Which scents do you like? Pick any.", {"field": "scent", "multi": True, "options": opts})
    if field == "occasion":
        opts = [(o.capitalize(), o) for o in OCCASIONS]
        return ("When will you wear it?", {"field": "occasion", "multi": False, "options": opts})
    if field == "budget":
        return ("What is your budget?", {"field": "budget", "multi": False,
                                         "options": [(a, b) for a, b in BUDGET_CHIPS]})
    return ("", None)


def apply_choice(state, field, values, kind_of):
    """Apply a tapped chip answer. values: list of strings. 'skip' marks the
    question as answered with nothing."""
    values = [str(v) for v in (values or [])][:8]
    if not values or values == ["skip"]:
        _mark(state, field)
        return True
    if field == "skin_type" and values[0] in SKIN_TYPES + ("unknown",):
        state["skin_type"] = values[0]
    elif field == "concerns":
        state["concerns"] = [v for v in values if v in CONCERNS]
    elif field == "scent":
        state["scent"] = [v for v in values if v in SCENTS]
    elif field == "occasion" and values[0] in OCCASIONS:
        state["occasion"] = values[0]
    elif field == "budget" and values[0] in BUDGET_RANGES:
        state["budget_min"], state["budget_max"] = BUDGET_RANGES[values[0]]
    else:
        return False
    _mark(state, field)
    return True


def summary_line(state, label):
    bits = [label.lower()]
    if state.get("skin_type") and state["skin_type"] != "unknown":
        bits.append("%s skin" % state["skin_type"])
    if state.get("concerns"):
        bits.append("for " + ", ".join(state["concerns"]))
    if state.get("budget_max"):
        bits.append("up to \u20A6{:,.0f}".format(state["budget_max"]))
    return ", ".join(bits)


# ---------------------------------------------------------------- limiters
_hits = {}
_ai_ip = {}
_ai_global = {"day": None, "n": 0}


def chat_rate_limited(client_id, limit=60, window=600, now=None):
    now = time.time() if now is None else now
    lst = [t for t in _hits.get(client_id, []) if now - t < window]
    if len(lst) >= limit:
        _hits[client_id] = lst
        return True
    lst.append(now)
    _hits[client_id] = lst
    if len(_hits) > 5000:  # keep memory bounded
        for k in list(_hits)[:1000]:
            _hits.pop(k, None)
    return False


def _today(now=None):
    return datetime.datetime.utcfromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d")


def ai_allowed(client_id, per_visitor, daily_cap, now=None):
    """True if this visitor and the whole shop still have free AI calls today.
    Counting happens in ai_spend(); this only checks."""
    day = _today(now)
    if _ai_global["day"] != day:
        _ai_global["day"], _ai_global["n"] = day, 0
        _ai_ip.clear()
    if _ai_global["n"] >= daily_cap:
        return False
    return _ai_ip.get(client_id, 0) < per_visitor


def ai_spend(client_id, now=None):
    day = _today(now)
    if _ai_global["day"] != day:
        _ai_global["day"], _ai_global["n"] = day, 0
        _ai_ip.clear()
    _ai_global["n"] += 1
    _ai_ip[client_id] = _ai_ip.get(client_id, 0) + 1
