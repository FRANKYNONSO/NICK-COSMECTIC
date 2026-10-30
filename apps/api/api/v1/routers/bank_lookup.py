"""Bank account name lookup (Paystack 'resolve account number').

The secret key is read from PAYSTACK_SECRET_KEY on the server only and is
never sent to the browser.
"""
import asyncio
import logging
import os
import re
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_current_user
from infrastructure.db.models import User

router = APIRouter(prefix="/bank-lookup", tags=["bank-lookup"])
log = logging.getLogger("bank_lookup")

_PAYSTACK = "https://api.paystack.co"
_BANKS_TTL = 86400
_NAME_TTL = 600
_banks_cache = {"at": 0.0, "items": []}
_name_cache = {}
_STOP = {"bank", "nigeria", "plc", "limited", "ltd", "of", "the", "for", "mfb", "microfinance"}
_ALIASES = {"opay": ["opay", "paycom"]}
# BANK-LOOKUP-COOLDOWN: pause Paystack calls after a 429 (rate limit)
_COOLDOWN_SECONDS = 300
_cooldown = {"until": 0.0}


def _forms(name):
    out = set()
    lowered = name.lower()
    base = re.sub(r"\([^)]*\)", " ", lowered)
    inner = re.findall(r"\(([^)]*)\)", lowered)
    for part in [base] + inner:
        words = [w for w in re.sub(r"[^a-z0-9 ]", " ", part).split() if w not in _STOP]
        if words:
            out.add(" ".join(words))
    return out


def _match_bank(our_name, banks):
    ours = _forms(our_name)
    extra = set()
    for f in ours:
        extra.update(_ALIASES.get(f, []))
    hits = []
    for b in banks:
        if ours & _forms(b["name"]):
            hits.append(b)
        elif extra and any(a in b["name"].lower() for a in extra):
            hits.append(b)
    if len(hits) > 1:
        plain = [b for b in hits if "(" not in b["name"]]
        if len(plain) == 1:
            hits = plain
    return hits[0] if len(hits) == 1 else None


async def _load_banks(client, headers):
    now = time.time()
    if _banks_cache["items"] and now - _banks_cache["at"] < _BANKS_TTL:
        return _banks_cache["items"]
    r = await client.get(_PAYSTACK + "/bank", params={"currency": "NGN", "perPage": 200}, headers=headers)
    if r.status_code != 200:
        log.warning("Paystack bank list failed (HTTP %s)", r.status_code)
        raise HTTPException(status_code=502, detail="Bank lookup is unavailable. Type the account name manually.")
    items = [
        {"name": str(b.get("name", "")), "code": str(b.get("code", ""))}
        for b in (r.json().get("data") or [])
        if b.get("name") and b.get("code")
    ]
    _banks_cache["items"] = items
    _banks_cache["at"] = now
    return items


_NUBAN_WEIGHTS = [3, 7, 3, 3, 7, 3, 3, 7, 3, 3, 7, 3]


def _nuban_ok(code, acct):
    """True if the 10th digit matches the NUBAN check digit for this 3-digit bank code."""
    if not re.fullmatch(r"\d{3}", code or ""):
        return False
    digits = [int(c) for c in code + acct[:9]]
    total = sum(d * w for d, w in zip(digits, _NUBAN_WEIGHTS))
    return (10 - total % 10) % 10 == int(acct[9])


@router.get("/suggest")
async def suggest_banks(
    account_number: str,
    bank_names: list[str] = Query(default=[]),
    _: User = Depends(get_current_user),
):
    acct = account_number.strip()
    if not re.fullmatch(r"\d{10}", acct):
        raise HTTPException(status_code=422, detail="Account number must be 10 digits.")
    secret = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
    if not secret:
        return {"suggestions": []}
    headers = {"Authorization": "Bearer " + secret}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            banks = await _load_banks(client, headers)
    except (HTTPException, httpx.HTTPError):
        return {"suggestions": []}
    out = []
    for n in bank_names[:100]:
        b = _match_bank(n, banks)
        if b and _nuban_ok(b["code"], acct):
            out.append(n)
    return {"suggestions": out}


# BANK-FIND: real lookup. Check digit filters, Paystack confirms with the account name.
_DIGITAL_BANKS = ["Opay", "PalmPay", "Moniepoint", "Kuda Bank"]
_FIND_TTL = 600
_FIND_MAX = 12
_find_cache = {}


async def _try_resolve(client, headers, bank, acct):
    """Return the account name if this bank owns the account, else None."""
    ckey = (bank["code"], acct)
    hit = _name_cache.get(ckey)
    if hit and time.time() - hit[0] < _NAME_TTL:
        return hit[1]
    if time.time() < _cooldown["until"]:
        return None
    try:
        r = await client.get(
            _PAYSTACK + "/bank/resolve",
            params={"account_number": acct, "bank_code": bank["code"]},
            headers=headers,
        )
    except httpx.HTTPError:
        return None
    if r.status_code == 429:
        _cooldown["until"] = time.time() + _COOLDOWN_SECONDS
        return None
    if r.status_code == 200:
        name = ((r.json().get("data") or {}).get("account_name") or "").strip()
        if name:
            _name_cache[ckey] = (time.time(), name)
            return name
    return None


@router.get("/find")
async def find_bank(
    account_number: str,
    bank_names: list[str] = Query(default=[]),
    _: User = Depends(get_current_user),
):
    acct = account_number.strip()
    if not re.fullmatch(r"\d{10}", acct):
        raise HTTPException(status_code=422, detail="Account number must be 10 digits.")
    empty = {"matches": [], "suggestions": [], "limited": False}
    secret = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
    if not secret:
        return empty
    cached = _find_cache.get(acct)
    if cached and time.time() - cached[0] < _FIND_TTL:
        return cached[1]
    headers = {"Authorization": "Bearer " + secret}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            banks = await _load_banks(client, headers)
            cands = []
            seen = set()
            for n in bank_names[:150]:
                b = _match_bank(n, banks)
                if b is None or b["code"] in seen:
                    continue
                if _nuban_ok(b["code"], acct) or n in _DIGITAL_BANKS:
                    seen.add(b["code"])
                    cands.append((n, b))
            cands = cands[:_FIND_MAX]
            matches = []
            for i in range(0, len(cands), 4):
                chunk = cands[i:i + 4]
                names = await asyncio.gather(
                    *[_try_resolve(client, headers, b, acct) for _, b in chunk]
                )
                for (n, b), nm in zip(chunk, names):
                    if nm:
                        matches.append({"bank_name": n, "account_name": nm})
                if matches or time.time() < _cooldown["until"]:
                    break
    except (HTTPException, httpx.HTTPError):
        return empty
    limited = time.time() < _cooldown["until"]
    result = {"matches": matches, "suggestions": [n for n, _ in cands], "limited": limited}
    if matches or not limited:
        if len(_find_cache) > 500:
            _find_cache.clear()
        _find_cache[acct] = (time.time(), result)
    return result


@router.get("/resolve")
async def resolve_account(
    bank_name: str,
    account_number: str,
    _: User = Depends(get_current_user),
):
    acct = account_number.strip()
    if not re.fullmatch(r"\d{10}", acct):
        raise HTTPException(status_code=422, detail="Account number must be 10 digits.")
    secret = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Bank name lookup is not set up. Type the account name manually.")
    headers = {"Authorization": "Bearer " + secret}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            banks = await _load_banks(client, headers)
            bank = _match_bank(bank_name, banks)
            if bank is None:
                raise HTTPException(status_code=404, detail="Could not match this bank automatically. Type the account name manually.")
            ckey = (bank["code"], acct)
            hit = _name_cache.get(ckey)
            if hit and time.time() - hit[0] < _NAME_TTL:
                return {"account_name": hit[1], "bank_matched": bank["name"]}
            if time.time() < _cooldown["until"]:
                raise HTTPException(status_code=502, detail="Bank lookup is paused for a few minutes because Paystack is limiting requests. Type the account name manually.")
            r = await client.get(
                _PAYSTACK + "/bank/resolve",
                params={"account_number": acct, "bank_code": bank["code"]},
                headers=headers,
            )
    except HTTPException:
        raise
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not reach the bank lookup service. Type the account name manually.")
    if r.status_code == 200:
        name = ((r.json().get("data") or {}).get("account_name") or "").strip()
        if name:
            _name_cache[ckey] = (time.time(), name)
            return {"account_name": name, "bank_matched": bank["name"]}
    if r.status_code in (400, 404, 422):
        raise HTTPException(status_code=404, detail="Could not verify this account number. Check it and the bank, or type the name manually.")
    log.warning("Paystack resolve failed (HTTP %s)", r.status_code)
    if r.status_code == 429:
        _cooldown["until"] = time.time() + _COOLDOWN_SECONDS
        raise HTTPException(status_code=502, detail="Paystack is limiting lookups right now. Type the account name manually; lookups resume in a few minutes.")
    raise HTTPException(status_code=502, detail="Bank lookup is unavailable. Type the account name manually.")