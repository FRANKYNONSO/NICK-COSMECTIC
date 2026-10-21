"""Optional Google Gemini helper for the Beauty Advisor (server side only).

Design rules
- GEMINI_API_KEY is OPTIONAL and read only from the environment. With no key,
  every function returns None and the advisor runs on its free built-in logic.
- Plain REST over httpx (already a dependency). No extra package to install.
- Nothing here raises. Any error, timeout or limit hit returns None.
- After a rate-limit (HTTP 429) or server error the helper switches itself off
  for a while, so the shop is not hammering Google and customers are not kept
  waiting. The advisor simply carries on without the AI.
- The AI never chooses products. It (1) reads a typed message into a few allowed
  fields and (2) rewords the reasons for products the engine already picked.
  Both outputs are validated against allow-lists by the caller.
- No photos are sent. Only the customer's typed sentence (max 300 characters)
  and our own product facts leave the server.
"""
import json
import os
import re
import time

import advisor_extras as extras

DEFAULT_MODEL = "gemini-3.5-flash-lite"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
_blocked_until = 0.0


def configured():
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def paused(now=None):
    return (time.time() if now is None else now) < _blocked_until


def _pause(seconds, now=None):
    global _blocked_until
    _blocked_until = (time.time() if now is None else now) + seconds


def _parts_text(data):
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return ""
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))


async def _call(httpx_module, prompt, schema, timeout=8.0):
    """Return parsed JSON (dict) or None. Never raises."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or paused():
        return None
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseFormat": {"text": {"mimeType": "application/json", "schema": schema}},
        },
    }
    try:
        async with httpx_module.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                ENDPOINT % model,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=body,
            )
        if r.status_code == 429:
            _pause(15 * 60)  # free daily or per-minute limit reached
            return None
        if r.status_code >= 500:
            _pause(2 * 60)
            return None
        if r.status_code >= 400:
            _pause(60)  # bad key / bad request: do not retry on every message
            return None
        text = _parts_text(r.json())
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        data = json.loads(text) if text else None
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ------------------------------------------------------- 1. read a typed message
def _extract_schema(slugs, skin_types, concerns, scents, occasions):
    return {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": list(slugs) + ["none"]},
            "skin_type": {"type": "string", "enum": list(skin_types) + ["unknown", "none"]},
            "concerns": {"type": "array", "items": {"type": "string", "enum": list(concerns)}},
            "scent": {"type": "array", "items": {"type": "string", "enum": list(scents)}},
            "occasion": {"type": "string", "enum": list(occasions) + ["none"]},
            "budget_max_naira": {"type": ["integer", "null"]},
            "medical_concern": {"type": "boolean"},
        },
        "required": ["category", "skin_type", "concerns", "scent", "occasion",
                     "budget_max_naira", "medical_concern"],
    }


def build_extract_prompt(message, categories):
    labels = ", ".join("%s (%s)" % (c["slug"], c["label"]) for c in categories)
    return (
        "You read one message from a customer of a cosmetics shop and fill a form. "
        "Use ONLY what the customer clearly said. If something was not said, use 'none', "
        "'unknown' or an empty list. Never guess. Never diagnose. "
        "Set medical_concern to true only if they describe an infection, rash, eczema, "
        "wound, severe pain or a similar medical problem. "
        "category must be one of these shop categories or 'none': %s. "
        "budget_max_naira is a number in Nigerian naira or null.\n\n"
        "Customer message: %s" % (labels, json.dumps(str(message)[:300]))
    )


async def read_message(httpx_module, message, categories, skin_types, concerns, scents, occasions):
    """Return a reading dict (same shape as advisor_flow.read_text) or None."""
    if not categories:
        return None
    slugs = [c["slug"] for c in categories]
    data = await _call(httpx_module, build_extract_prompt(message, categories),
                       _extract_schema(slugs, skin_types, concerns, scents, occasions))
    if not data:
        return None
    out = {}
    if data.get("category") in slugs:
        out["category"] = data["category"]
    if data.get("skin_type") in skin_types + ("unknown",):
        out["skin_type"] = data["skin_type"]
    cs = [c for c in (data.get("concerns") or []) if c in concerns]
    if cs:
        out["concerns"] = cs[:8]
    sc = [c for c in (data.get("scent") or []) if c in scents]
    if sc:
        out["scent"] = sc[:4]
    if data.get("occasion") in occasions:
        out["occasion"] = data["occasion"]
    b = data.get("budget_max_naira")
    if isinstance(b, (int, float)) and not isinstance(b, bool) and 100 <= b <= 10_000_000:
        out["budget"] = (0, float(b))
    if data.get("medical_concern") is True:
        out["medical"] = True
    return out


# ------------------------------------------------------- 2. reword reasons
_REASON_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"product_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["product_id", "reason"],
            },
        }
    },
    "required": ["items"],
}


async def reword_reasons(httpx_module, shortlist, answers):
    """Return a JSON string {"items":[{product_id, reason}]} or None. The
    caller must validate it with advisor_engine.validate_llm_output."""
    if not shortlist:
        return None
    system, user = extras.build_llm_prompt(shortlist, answers)
    data = await _call(httpx_module, system + "\n\n" + user, _REASON_SCHEMA)
    return json.dumps(data) if data else None
