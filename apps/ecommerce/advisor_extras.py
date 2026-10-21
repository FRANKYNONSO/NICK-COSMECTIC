"""Helpers for the skincare advisor: input cleaning, weather, optional AI wording.

Rules kept here:
- WEATHER_API_KEY and AI_API_KEY are OPTIONAL. Any failure returns None / {}
  and the advisor simply gives plainer results. Nothing here can stop the
  storefront from starting or break browsing, cart or checkout.
- Keys are read from environment variables only. Never printed, never sent
  to the browser.
- Precise location (lat/lon) is used once for the weather call and is
  never stored or logged.
"""
import json
import os
import re
import time

ALLOWED_SKIN = ("dry", "oily", "combination", "normal", "sensitive", "unknown")
ALLOWED_CONCERNS = (
    "dryness", "oil control", "dark spots", "acne", "sun protection",
    "brightening", "sensitive skin", "baby care",
)
ALLOWED_ROUTINE = ("indoor", "outdoor", "mixed", "unknown")
ALLOWED_SUN = ("low", "medium", "high", "unknown")
ALLOWED_SWEAT = ("low", "high", "unknown")
ALLOWED_TYPES = ("any", "lotion", "soap", "oil", "makeup")
# budget band -> (min, max) in naira; None = no limit
BUDGETS = {
    "any": (0, None),
    "under_3000": (0, 3000),
    "3000_8000": (3000, 8000),
    "8000_20000": (8000, 20000),
    "over_20000": (20000, None),
}

_NAME_RE = re.compile(r"[^A-Za-z .'\-]")


def _pick(value, allowed, default):
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def clean_place(value):
    s = _NAME_RE.sub("", str(value or "")).strip()
    return s[:60]


def sanitize_answers(raw):
    """Turn untrusted browser JSON into a safe answers dict."""
    raw = raw if isinstance(raw, dict) else {}
    concerns = []
    for c in (raw.get("concerns") or [])[:8]:
        c = str(c or "").strip().lower()
        if c in ALLOWED_CONCERNS and c not in concerns:
            concerns.append(c)
    band = _pick(raw.get("budget"), tuple(BUDGETS), "any")
    lo, hi = BUDGETS[band]
    ptype = _pick(raw.get("product_type"), ALLOWED_TYPES, "any")
    out = {
        "skin_type": _pick(raw.get("skin_type"), ALLOWED_SKIN, "unknown"),
        "concerns": concerns,
        "routine": _pick(raw.get("routine"), ALLOWED_ROUTINE, "unknown"),
        "sun_exposure": _pick(raw.get("sun_exposure"), ALLOWED_SUN, "unknown"),
        "sweating": _pick(raw.get("sweating"), ALLOWED_SWEAT, "unknown"),
        "product_type": ptype,
        "budget": band,
        "budget_min": lo,
        "budget_max": hi,
        "state": clean_place(raw.get("state")),
        "city": clean_place(raw.get("city")),
    }
    if "baby care" in concerns:
        out["product_type"] = ptype  # baby items are allowed through by the engine
    # device location: used for ONE weather call, never stored
    try:
        lat, lon = float(raw.get("lat")), float(raw.get("lon"))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out["lat"], out["lon"] = lat, lon
    except (TypeError, ValueError):
        pass
    return out


# ---------------------------------------------------------------- weather
def weather_signal(temp_c, humidity):
    """One signal from live readings. Nothing about any city is hard-coded."""
    try:
        t, h = float(temp_c), float(humidity)
    except (TypeError, ValueError):
        return None
    if t >= 30 and h >= 60:
        return "hot_humid"
    if t >= 30:
        return "hot_dry"
    if h >= 70:
        return "humid"
    if h < 40:
        return "dry"
    return "mild"


WEATHER_LABELS = {
    "hot_humid": "hot and humid",
    "hot_dry": "hot and dry",
    "humid": "humid",
    "dry": "dry",
    "mild": "mild",
}

_cache = {}
_CACHE_SECONDS = 30 * 60


async def fetch_weather(httpx_module, answers):
    """Return {"signal": ..., "label": ...} or None. Never raises."""
    key = os.environ.get("WEATHER_API_KEY", "").strip()
    if not key:
        return None
    params = {"appid": key, "units": "metric"}
    cache_key = None
    if "lat" in answers:
        params["lat"], params["lon"] = round(answers["lat"], 2), round(answers["lon"], 2)
    elif answers.get("city"):
        params["q"] = answers["city"] + ",NG"
        cache_key = params["q"].lower()
    else:
        return None
    now = time.time()
    if cache_key and cache_key in _cache and now - _cache[cache_key][0] < _CACHE_SECONDS:
        return _cache[cache_key][1]
    try:
        async with httpx_module.AsyncClient(timeout=4.0) as client:
            r = await client.get("https://api.openweathermap.org/data/2.5/weather", params=params)
            r.raise_for_status()
            main = r.json().get("main", {})
        sig = weather_signal(main.get("temp"), main.get("humidity"))
        if not sig:
            return None
        result = {"signal": sig, "label": WEATHER_LABELS[sig]}
        if cache_key:
            _cache[cache_key] = (now, result)
        return result
    except Exception:
        return None


# ------------------------------------------------------------ optional AI
def build_llm_prompt(shortlist, answers):
    facts = [
        {"product_id": s["product_id"], "name": s["name"], "verified_facts": s["reasons"]}
        for s in shortlist
    ]
    system = (
        "You write short, friendly product explanations for a cosmetics shop. "
        "Use ONLY the products and verified_facts given. Never mention any other "
        "product, brand or ingredient. Never diagnose or promise results; use words "
        "like 'may suit'. At most two sentences per product. "
        'Reply with JSON only: {"items":[{"product_id":"...","reason":"..."}]}'
    )
    user = json.dumps({
        "customer": {
            "skin_type": answers.get("skin_type"),
            "concerns": answers.get("concerns"),
            "wants": answers.get("product_type"),
        },
        "products": facts,
    })
    return system, user


def parse_llm_text(text):
    """Strip code fences; return the JSON string (validated later)."""
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    return t


async def ask_llm(httpx_module, shortlist, answers):
    """Return raw JSON text from the model, or None. Never raises."""
    key = os.environ.get("AI_API_KEY", "").strip()
    if not key or not shortlist:
        return None
    system, user = build_llm_prompt(shortlist, answers)
    try:
        async with httpx_module.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": os.environ.get("AI_MODEL", "claude-sonnet-4-6"),
                      "max_tokens": 600, "system": system,
                      "messages": [{"role": "user", "content": user}]},
            )
            r.raise_for_status()
            blocks = r.json().get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return parse_llm_text(text) or None
    except Exception:
        return None


# ------------------------------------------------------------ rate limit
_hits = {}
LIMIT, WINDOW = 20, 600


def too_many(client_id, now=None):
    """Sliding window: 20 advisor requests per 10 minutes per client."""
    now = time.time() if now is None else now
    lst = [t for t in _hits.get(client_id, []) if now - t < WINDOW]
    if len(lst) >= LIMIT:
        _hits[client_id] = lst
        return True
    lst.append(now)
    _hits[client_id] = lst
    return False
