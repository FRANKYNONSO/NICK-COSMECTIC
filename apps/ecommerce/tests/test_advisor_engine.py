import json, copy, sys
sys.path.insert(0, ".")
import advisor_engine as E

src = json.load(open("/mnt/user-data/uploads/products_export.json", encoding="utf-8-sig"))["value"]
products = [p for p in src if p["status"] == "active" and p["price_naira"] > 0]
stock = {p["product_id"]: p["quantity_available"] for p in src}
stock_real = dict(stock)
real_meta = E.load_catalog_meta("advisor_catalog.json")

byname = {p["name"]: p["product_id"] for p in products}
def pid(n): return byname[n]
stock[pid("Almond Lotion Herbal")] = 10  # test only: real export shows 0

# TEST-ONLY facts (made up for this test, NOT written to the real catalog file)
meta = copy.deepcopy(real_meta)
def facts(name, **kw): meta[pid(name)].update(kw)
facts("Almond Lotion Herbal", skin_types=["dry"], concerns=["dryness"])
facts("Miss white lotion", skin_types=["oily", "combination"], concerns=["oil control"])
facts("So White lotion", skin_types=["all"], spf="30", concerns=["sun protection"])
facts("Pure and white Gold lotion", skin_types=["normal"])

def names(r): return [i["name"] for i in r["items"]]
ok = 0
def check(label, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + label); ok += 0 if cond else 1

# 1 Jalingo outdoor/dry, hot dry weather, wants lotion
r = E.recommend({"skin_type":"dry","product_type":"lotion","sun_exposure":"high","concerns":["dryness"]}, products, stock, meta, {"signal":"hot_dry"})
check("1 Jalingo dry/outdoor: dry lotion and sun-protect lotion lead", set(names(r)[:2]) == {"Almond Lotion Herbal","So White lotion"})
# 2 Lagos indoor oily humid
r = E.recommend({"skin_type":"oily","product_type":"lotion","concerns":["oil control"]}, products, stock, meta, {"signal":"humid"})
check("2 Lagos oily humid: oil-control lotion first", names(r)[0] == "Miss white lotion")
check("2b dry-only lotion not pushed to oily user", "Almond Lotion Herbal" not in names(r))
# 3 Abuja combination
r = E.recommend({"skin_type":"combination","product_type":"lotion"}, products, stock, meta, {"signal":"mild"})
check("3 Abuja combination: Miss white first", names(r)[0] == "Miss white lotion")
# 4 unknown skin type
r = E.recommend({"skin_type":"unknown","product_type":"soap"}, products, stock, meta)
check("4 unknown skin: soaps returned, none invented", len(r["items"])>0 and all(i["name"] in [p["name"] for p in products] for i in r["items"]))
# 5 no location / 6 weather down -> weather None works
r = E.recommend({"skin_type":"dry","product_type":"lotion"}, products, stock, meta, None)
check("5/6 no location / weather API down: still works", len(r["items"])>0)
# 7 AI down: invalid LLM output -> {} and engine untouched
check("7 AI down / garbage output: validator returns empty", E.validate_llm_output("<html>502</html>", r["items"], []) == {})
# 8 recommended item out of stock -> next one
s2 = dict(stock); s2[pid("Almond Lotion Herbal")] = 0
r = E.recommend({"skin_type":"dry","product_type":"lotion","concerns":["dryness"]}, products, s2, meta)
check("8 out of stock never recommended", "Almond Lotion Herbal" not in names(r) and len(r["items"])>0)
print("   (real export has Almond Lotion Herbal at 0:", stock_real[pid("Almond Lotion Herbal")] == 0, ")")
# 9 no matches
r = E.recommend({"skin_type":"dry","product_type":"lotion","budget_min":900000}, products, stock, meta)
check("9 no matches: empty list + plain message", r["items"]==[] and "do not have a suitable" in r["message"])
# 10 LLM names product not in shortlist / unknown id
r = E.recommend({"skin_type":"dry","product_type":"lotion"}, products, stock, meta)
sl = r["items"]; a, b = sl[0], sl[1]
cat_names = [p["name"] for p in products]
raw = json.dumps({"items":[
  {"product_id": a["product_id"], "reason": "Gentle choice for your skin."},
  {"product_id": "not-in-list", "reason": "Try CeraVe cream."},
  {"product_id": b["product_id"], "reason": "Also consider Tumeric and honey soap with it."},
]})
v = E.validate_llm_output(raw, sl, cat_names)
check("10 LLM unknown id dropped, reason naming other catalog item dropped", list(v)==[a["product_id"]])
# extras
check("baby products hidden unless asked", all("baby" not in n.lower() for n in names(E.recommend({"product_type":"any"}, products, stock, meta))))
r = E.recommend({"product_type":"any","concerns":["baby care"]}, products, stock, meta)
check("baby products shown when baby care chosen", any("baby" in n.lower() for n in names(r)))
check("mice / unenabled items never appear", all(n not in ("mice","T-shirt","A56") for n in names(E.recommend({"product_type":"any"}, products, stock, meta, limit=50))))
r = E.recommend({"product_type":"lotion"}, products, stock, real_meta)
check("no-facts catalog: honest reason, details_on_file False", r["items"] and all(not i["details_on_file"] for i in r["items"]) and any("do not have more details" in x for x in r["items"][0]["reasons"]))
print("FAILED:", ok)
