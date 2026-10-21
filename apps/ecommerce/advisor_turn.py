"""One Beauty Advisor chat turn. No web framework in here, so it can be tested
on its own. The router (routers/advisor_chat.py) only adds HTTP around it.

Input : the browser's request body, the visitor's session dict, the live
        product list + stock, and the advisor catalog entries.
Output: a JSON-ready dict: messages, optional chips, optional product cards.

Safety by construction
- Products shown are only those picked by advisor_engine (enabled, active,
  priced, in stock). The AI cannot add or change a product.
- The AI is optional and limited (per visitor and shop-wide each day).
- Texts shown to the customer come from this file or from the validated
  per-product reasons, never from free AI prose.
"""
import os

import advisor_engine as engine
import advisor_flow as flow
import advisor_gemini as gemini

GREETING = (
    "Hi, I am the Nick Cosmetics Beauty Advisor. Tell me what you are looking for, "
    "or pick a category below. I only suggest products that we have in stock right now."
)


def _int_env(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _chips(spec, skippable=False):
    if not spec:
        return None
    return {
        "field": spec["field"],
        "multi": spec["multi"],
        "skippable": skippable,
        "options": [{"label": a, "value": b} for a, b in spec["options"]],
    }


def _category_chips(cats):
    return {
        "field": "category", "multi": False, "skippable": False,
        "options": [{"label": c["label"], "value": c["slug"]} for c in cats],
    }


def _reply(messages, chips=None, items=None, disclaimer=False, ai_used=False):
    return {"ok": True, "messages": messages, "chips": chips, "items": items or [],
            "disclaimer": disclaimer, "ai_used": ai_used}


async def handle(body, session, visitor_id, products, stock, meta, httpx_module):
    body = body if isinstance(body, dict) else {}
    action = str(body.get("action") or "")
    cats = flow.available_categories(products, stock, meta)
    by_slug = {c["slug"]: c for c in cats}
    state = flow.clean_state(session.get("adv_chat"), set(by_slug))
    kind_of = lambda slug: by_slug[slug]["kind"] if slug in by_slug else "other"  # noqa: E731

    if not cats:
        return _reply(["Our advisor has nothing in stock to suggest right now. "
                       "Please browse the shop, or ask us in store."])

    if action in ("start", "reset"):
        session["adv_chat"] = flow.new_state()
        return _reply([GREETING], chips=_category_chips(cats))

    messages = []
    show_now = False
    ai_used = False
    medical = False

    if action == "choose":
        field = str(body.get("field") or "")
        values = body.get("values")
        if not isinstance(values, list):
            values = [body.get("value")] if body.get("value") is not None else []
        values = [str(v) for v in values][:8]
        if field == "next":
            if values and values[0] == "restart":
                session["adv_chat"] = flow.new_state()
                return _reply([GREETING], chips=_category_chips(cats))
            state = flow.new_state()
            session["adv_chat"] = state
            return _reply(["Which category would you like to look at?"], chips=_category_chips(cats))
        if field == "category":
            slug = values[0] if values else ""
            if slug not in by_slug:
                return _reply(["Please pick one of the categories below."], chips=_category_chips(cats))
            state["category"] = slug
            messages.append("Good choice.")
        else:
            if not state["category"]:
                return _reply(["Let us start with a category."], chips=_category_chips(cats))
            if not flow.apply_choice(state, field, values, kind_of):
                nf = flow.next_field(state, kind_of(state["category"]))
                text, spec = flow.question_for(nf, state) if nf else ("", None)
                return _reply(["I did not understand that choice. " + text], chips=_chips(spec, True))
    elif action == "text":
        text = "".join(ch for ch in str(body.get("text") or "") if ch.isprintable())[:300].strip()
        if not text:
            return _reply(["Type a message, or pick one of the options."],
                          chips=_category_chips(cats) if not state["category"] else None)
        reading = flow.read_text(text, cats)
        understood = any(k in reading for k in
                         ("category", "skin_type", "concerns", "scent", "occasion", "budget", "medical"))
        if not understood and gemini.configured() and not gemini.paused() and flow.ai_allowed(
                visitor_id, _int_env("ADVISOR_AI_PER_VISITOR", 12), _int_env("ADVISOR_AI_DAILY_CAP", 150)):
            flow.ai_spend(visitor_id)
            ai_read = await gemini.read_message(
                httpx_module, text, cats, flow.SKIN_TYPES, flow.CONCERNS, flow.SCENTS, flow.OCCASIONS)
            if ai_read:
                reading = {**reading, **ai_read}
                ai_used = True
                understood = any(k in ai_read for k in
                                 ("category", "skin_type", "concerns", "scent", "occasion", "budget", "medical"))
        if reading.get("medical"):
            medical = True
            messages.append(flow.MEDICAL_REPLY)
        if not understood and not reading.get("show_now"):
            nf = flow.next_field(state, kind_of(state["category"])) if state["category"] else None
            if nf:
                qtext, spec = flow.question_for(nf, state)
                return _reply(["I did not quite catch that. " + qtext], chips=_chips(spec, True))
            return _reply(["I did not quite catch that. Tell me the product and your skin needs, "
                           "or pick a category."], chips=_category_chips(cats))
        flow.apply_reading(state, reading, kind_of)
        show_now = bool(reading.get("show_now"))
        if state["category"] and not medical:
            messages.append("Got it.")
    else:
        return _reply([GREETING], chips=_category_chips(cats))

    # ---- decide what comes next
    if not state["category"]:
        session["adv_chat"] = state
        messages.append("Which category would you like to look at?")
        return _reply(messages, chips=_category_chips(cats), ai_used=ai_used)

    kind = kind_of(state["category"])
    nf = flow.next_field(state, kind)
    if nf and not show_now:
        session["adv_chat"] = state
        qtext, spec = flow.question_for(nf, state)
        messages.append(qtext)
        return _reply(messages, chips=_chips(spec, nf != "budget"), ai_used=ai_used, disclaimer=medical)

    # ---- recommend
    session["adv_chat"] = state
    label = by_slug[state["category"]]["label"]
    result = flow.pick_products(state, products, stock, meta)
    items = result["items"]
    after_chips = {"field": "next", "multi": False, "skippable": False,
                   "options": [{"label": "Another category", "value": "category"},
                               {"label": "Start over", "value": "restart"}]}
    if not items:
        messages.append(result.get("message") or "I could not find a match right now.")
        if state.get("budget_max") or state.get("budget_min"):
            wide = dict(state, budget_min=None, budget_max=None)
            alt = flow.pick_products(wide, products, stock, meta, limit=50)["items"]
            if alt:
                low = min(a["price_naira"] for a in alt)
                messages.append("The lowest-priced option in stock in this category is \u20A6{:,.0f}, so a higher budget would help.".format(low))
        messages.append("You can change your answers, try another category, or browse the whole shop.")
        return _reply(messages, chips=after_chips, ai_used=ai_used, disclaimer=medical)

    answers = flow.engine_answers(state)
    if gemini.configured() and not gemini.paused() and flow.ai_allowed(
            visitor_id, _int_env("ADVISOR_AI_PER_VISITOR", 12), _int_env("ADVISOR_AI_DAILY_CAP", 150)):
        flow.ai_spend(visitor_id)
        raw = await gemini.reword_reasons(httpx_module, items, answers)
        if raw:
            names = [p.get("name") for p in products]
            good = engine.validate_llm_output(raw, items, names)
            for it in items:
                if it["product_id"] in good:
                    it["reasons"] = [good[it["product_id"]]]
                    ai_used = True

    by_id = {str(p.get("id")): p for p in products}
    cards = []
    for it in items:
        p = by_id.get(it["product_id"], {})
        cards.append({
            "product_id": it["product_id"],
            "name": it["name"],
            "size": it.get("size"),
            "price_naira": it["price_naira"],
            "reasons": it["reasons"][:3],
            "details_on_file": it["details_on_file"],
            "image_url": p.get("image_url"),
            "url": "/product/" + it["product_id"],
            "in_stock": True,
        })
    if any(c["details_on_file"] for c in cards):
        messages.append("Here are my picks for %s. All of them are in stock now." % flow.summary_line(state, label))
    else:
        # Be honest: with no skin or ingredient facts on file, products are only
        # filtered by category, stock and budget, not matched to skin needs.
        messages.append(
            "Here is what we have in stock in %s%s. We have not added skin details for these products "
            "yet, so I cannot say which one suits your skin best. Please check each product's label."
            % (label, " within your budget" if (state.get("budget_max") or state.get("budget_min")) else ""))
    return _reply(messages, chips=after_chips, items=cards, ai_used=ai_used,
                  disclaimer=(kind == "skin") or medical)
