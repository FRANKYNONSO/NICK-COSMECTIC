"use strict";
// PATCHP3P-TESTS: checks web_admin/js/staff-picker.js (pure helpers, no page).
// Run: node check_staff_picker.js
// Optional: set STAFF_PICKER_FILE to check another copy of the file.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FILE = process.env.STAFF_PICKER_FILE ||
  path.join(__dirname, "..", "..", "web_admin", "js", "staff-picker.js");
if (!fs.existsSync(FILE)) {
  console.log("FAIL staff-picker.js is missing: " + FILE);
  process.exit(1);
}
const SRC = fs.readFileSync(FILE, "utf8");
const SP = require(path.resolve(FILE));

let total = 0;
let failures = 0;
function check(label, ok) {
  total++;
  console.log((ok ? "PASS " : "FAIL ") + label);
  if (!ok) failures++;
}
function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function parseBoxes(html) {
  const out = [];
  const re = /data-perm="([^"]+)" (checked)?/g;
  let m;
  while ((m = re.exec(html))) out.push({ key: m[1], checked: !!m[2] });
  return out;
}
function boxFor(html, key) { return parseBoxes(html).find(function (b) { return b.key === key; }); }

const REG = [
  { group: "Coupons", tabs: [{ tab: "", permissions: [{ key: "manage_coupons", label: "Issue & void coupons" }] }] },
  { group: "Inventory", tabs: [{ tab: "Stock", permissions: [
    { key: "manage_inventory", label: "Manage inventory & warehouse" },
    { key: "view_stock", label: "View stock" } ] }] },
  { group: "POS", tabs: [{ tab: "", permissions: [
    { key: "process_sale", label: "Process sales" },
    { key: "search_products", label: "Search products" } ] }] },
  { group: "Reports", tabs: [{ tab: "", permissions: [
    { key: "view_financials", label: "View financial totals" },
    { key: "view_all_reports", label: "View store-wide reports" } ] }] },
  { group: "Team", tabs: [
    { tab: "Own account", permissions: [{ key: "change_own_password", label: "Change own password" }] },
    { tab: "Staff", permissions: [
      { key: "manage_users", label: "Manage staff accounts" },
      { key: "remove_super_admin", label: "Remove super admin" } ] } ] },
];
const CASHIER = ["process_sale", "view_stock", "manage_coupons", "change_own_password"];

// ---- A: loading
const ctx = vm.createContext({});
vm.runInContext(SRC, ctx);
check("A1 loads as a plain browser script and defines StaffPicker", vm.runInContext("typeof StaffPicker", ctx) === "object");
check("A2 exports the helpers in Node", typeof SP.renderBoxes === "function" && typeof SP.mergeSelection === "function");

// ---- B: the agreed key lists
const WANT_BLOCKED = ["view_financials", "view_revenue_totals", "view_all_reports", "view_audit_logs",
  "download_statements", "create_staff", "review_staff_applications", "review_password_resets",
  "confirm_theft_losses", "manage_backups"];
check("B1 blocked grant list is exactly the 10 agreed keys", same(SP.BLOCKED_GRANT_KEYS.slice().sort(), WANT_BLOCKED.slice().sort()));
check("B2 never-restrict list is process_sale, view_own_sales, change_own_password",
  same(SP.NEVER_RESTRICT_KEYS.slice().sort(), ["change_own_password", "process_sale", "view_own_sales"]));
check("B3 owner-only list is remove_super_admin, transfer_ownership",
  same(SP.OWNER_ONLY_KEYS.slice().sort(), ["remove_super_admin", "transfer_ownership"]));

// ---- C: isUsableRegistry
check("C1 a good registry is usable", SP.isUsableRegistry(REG) === true);
check("C2 an empty list is not usable", SP.isUsableRegistry([]) === false);
check("C3 null is not usable", SP.isUsableRegistry(null) === false);
check("C4 a group with no tabs is not usable", SP.isUsableRegistry([{ group: "X", tabs: [] }]) === false);
check("C5 a permission without a key is not usable",
  SP.isUsableRegistry([{ group: "X", tabs: [{ tab: "", permissions: [{ label: "no key" }] }] }]) === false);
check("C6 a plain object is not usable", SP.isUsableRegistry({}) === false);

// ---- D: roleKeysFor
const ROLES = [
  { name: "cashier", permissions: CASHIER.slice() },
  { name: "empty", permissions: [] },
  { name: "broken", permissions: null },
];
check("D1 returns the keys of a known role", same(SP.roleKeysFor(ROLES, "cashier"), CASHIER));
const copyKeys = SP.roleKeysFor(ROLES, "cashier");
copyKeys.push("extra_key");
check("D2 returns a copy, not the role's own list", same(ROLES[0].permissions, CASHIER));
check("D3 unknown role gives null", SP.roleKeysFor(ROLES, "nobody") === null);
check("D4 a role with a broken list gives null", SP.roleKeysFor(ROLES, "broken") === null);
check("D5 roles that are not a list give null", SP.roleKeysFor(null, "cashier") === null);
check("D6 an empty role gives an empty list", same(SP.roleKeysFor(ROLES, "empty"), []));

// ---- E: Restrict list
const r = SP.renderBoxes("restrict", REG, CASHIER, ["manage_coupons"]);
check("E1 restrict shows only keys the role holds (registry order)", same(r.shown, ["manage_coupons", "view_stock"]));
check("E2 process_sale is never offered", r.shown.indexOf("process_sale") === -1);
check("E3 change_own_password is never offered", r.shown.indexOf("change_own_password") === -1);
check("E4 a key the role lacks is not offered", r.shown.indexOf("manage_inventory") === -1);
check("E5 an owner-only key is not offered", r.shown.indexOf("remove_super_admin") === -1);
check("E6 a denied key is unticked", boxFor(r.html, "manage_coupons") && boxFor(r.html, "manage_coupons").checked === false);
check("E7 an allowed key is ticked", boxFor(r.html, "view_stock") && boxFor(r.html, "view_stock").checked === true);
check("E8 group and tab headings are drawn",
  r.html.indexOf(">Coupons<") !== -1 && r.html.indexOf(">Inventory<") !== -1 && r.html.indexOf(">Stock<") !== -1);
check("E9 groups with nothing to show are left out",
  r.html.indexOf(">Reports<") === -1 && r.html.indexOf(">POS<") === -1);
check("E10 no empty tab heading is drawn", !/<div style="[^"]*">\s*<\/div>/.test(r.html));

// ---- F: Grant list
const g = SP.renderBoxes("grant", REG, CASHIER, ["manage_inventory"]);
check("F1 grant shows keys the role lacks (registry order)", same(g.shown, ["manage_inventory", "search_products", "manage_users"]));
check("F2 blocked keys are not offered",
  g.shown.indexOf("view_financials") === -1 && g.shown.indexOf("view_all_reports") === -1);
check("F3 an owner-only key is not offered", g.shown.indexOf("remove_super_admin") === -1);
check("F4 keys the role already holds are not offered", g.shown.indexOf("view_stock") === -1 && g.shown.indexOf("process_sale") === -1);
check("F5 a granted key is ticked", boxFor(g.html, "manage_inventory") && boxFor(g.html, "manage_inventory").checked === true);
check("F6 a key not granted is unticked", boxFor(g.html, "manage_users") && boxFor(g.html, "manage_users").checked === false);
check("F7 no 'Already granted' section when no blocked key is granted", g.html.indexOf("Already granted") === -1);
const g2 = SP.renderBoxes("grant", REG, CASHIER, ["view_financials"]);
check("F8 a blocked key that is already granted is shown, ticked, with its registry label",
  g2.shown.indexOf("view_financials") !== -1 && boxFor(g2.html, "view_financials").checked === true &&
  g2.html.indexOf("Already granted") !== -1 && g2.html.indexOf("View financial totals") !== -1);
const g3 = SP.renderBoxes("grant", REG, CASHIER, ["view_audit_logs"]);
check("F9 a granted blocked key that is not in the registry is still shown", g3.shown.indexOf("view_audit_logs") !== -1);
const BIG = [{ group: "All", tabs: [{ tab: "", permissions:
  SP.BLOCKED_GRANT_KEYS.concat(SP.OWNER_ONLY_KEYS, ["normal_key"]).map(function (k) { return { key: k, label: k }; }) }] }];
check("F10 with nothing granted, only the normal key is offered", same(SP.renderBoxes("grant", BIG, [], []).shown, ["normal_key"]));
check("F11 restrict never offers owner-only keys",
  same(SP.renderBoxes("restrict", BIG, SP.OWNER_ONLY_KEYS.concat(["normal_key"]), []).shown, ["normal_key"]));
const ALL_KEYS = [];
REG.forEach(function (grp) { grp.tabs.forEach(function (t) { t.permissions.forEach(function (p) { ALL_KEYS.push(p.key); }); }); });
const full = SP.renderBoxes("grant", REG, ALL_KEYS, []);
check("F12 a role that holds every key gives an empty grant list with a message",
  same(full.shown, []) && full.html.indexOf("Nothing to show") !== -1 && parseBoxes(full.html).length === 0);

// ---- G: escaping and duplicates
const EVIL = [{ group: "<b>G</b>", tabs: [{ tab: "<i>T</i>", permissions: [{ key: 'a"b', label: "<script>x</script>" }] }] }];
const ev = SP.renderBoxes("restrict", EVIL, ['a"b'], []);
check("G1 a label is escaped", ev.html.indexOf("<script>") === -1 && ev.html.indexOf("&lt;script&gt;") !== -1);
check("G2 a group heading is escaped", ev.html.indexOf("<b>G") === -1 && ev.html.indexOf("&lt;b&gt;G") !== -1);
check("G3 a key with a quote is escaped in the attribute", ev.html.indexOf('data-perm="a&quot;b"') !== -1);
check("G4 shown keeps the raw key", same(ev.shown, ['a"b']));
const DUP = [
  { group: "A", tabs: [{ tab: "", permissions: [{ key: "k1", label: "One" }] }] },
  { group: "B", tabs: [{ tab: "", permissions: [{ key: "k1", label: "One again" }] }] },
];
const dup = SP.renderBoxes("restrict", DUP, ["k1"], []);
check("G5 a key listed twice is drawn once", parseBoxes(dup.html).length === 1 && same(dup.shown, ["k1"]));

// ---- H: mergeSelection
check("H1 restrict: unticked shown key is denied, unseen denied key stays",
  same(SP.mergeSelection("restrict", ["a", "b"], ["a"], ["b", "z"]), ["b", "z"]));
check("H2 restrict: all ticked clears shown keys, unseen key stays",
  same(SP.mergeSelection("restrict", ["a", "b"], ["a", "b"], ["z"]), ["z"]));
check("H3 restrict: a new untick is added", same(SP.mergeSelection("restrict", ["a", "b"], ["a"], []), ["b"]));
check("H4 grant: ticked shown key is granted, unseen granted key stays",
  same(SP.mergeSelection("grant", ["a", "b"], ["a"], ["b", "z"]), ["a", "z"]));
check("H5 grant: unticking removes a shown key, unseen key stays",
  same(SP.mergeSelection("grant", ["a", "b"], [], ["a", "b", "z"]), ["z"]));
check("H6 null inputs give an empty list", same(SP.mergeSelection("grant", null, null, null), []));
check("H7 result is sorted with no duplicates", same(SP.mergeSelection("grant", ["b", "a"], ["b", "a"], ["a"]), ["a", "b"]));

// ---- I: open and save with no clicks changes nothing
function roundTrip(mode, reg, role, prev) {
  const rr = SP.renderBoxes(mode, reg, role, prev);
  const ticked = parseBoxes(rr.html).filter(function (b) { return b.checked; }).map(function (b) { return b.key; });
  return SP.mergeSelection(mode, rr.shown, ticked, prev);
}
check("I1 restrict round trip keeps a denied key and an unknown denied key",
  same(roundTrip("restrict", REG, CASHIER, ["manage_coupons", "legacy_key"]), ["legacy_key", "manage_coupons"]));
check("I2 grant round trip keeps a granted key and an unknown granted key",
  same(roundTrip("grant", REG, CASHIER, ["manage_inventory", "legacy_x"]), ["legacy_x", "manage_inventory"]));
check("I3 grant round trip keeps an already granted blocked key",
  same(roundTrip("grant", REG, CASHIER, ["view_financials", "manage_users"]), ["manage_users", "view_financials"]));
check("I4 restrict round trip keeps a denial of a key the role lacks",
  same(roundTrip("restrict", REG, CASHIER, ["manage_inventory"]), ["manage_inventory"]));
check("I5 empty lists stay empty in both modes",
  same(roundTrip("restrict", REG, CASHIER, []), []) && same(roundTrip("grant", REG, CASHIER, []), []));

console.log("\n" + (total - failures) + " passed, " + failures + " failed, " + total + " checks");
process.exit(failures ? 1 : 0);
