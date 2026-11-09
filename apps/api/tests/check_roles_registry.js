"use strict";
// PATCHP3O-TESTS: checks the grouped permission boxes on the Roles screen
// (web_admin/js/roles.js) against a tiny fake page and a fake registry.
// Run: node check_roles_registry.js
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(path.join(__dirname, "..", "..", "web_admin", "js", "roles.js"), "utf8");

let failures = 0;
function check(label, ok) {
  console.log((ok ? "PASS " : "FAIL ") + label);
  if (!ok) failures++;
}

const ROLES = [
  { id: "1", name: "super_admin", display_name: "Super Admin", permissions: ["process_sale"], is_system: true, user_count: 1 },
  { id: "2", name: "cashier", display_name: "Cashier", permissions: ["process_sale", "legacy_thing"], is_system: true, user_count: 3 },
  { id: "3", name: "custom_one", display_name: "Custom One", permissions: ["view_stock"], is_system: false, user_count: 0 },
];

const REGISTRY = [
  { group: "Sales", tabs: [
    { tab: "POS", permissions: [
      { key: "process_sale", label: "Process sales (server)" },
      { key: "view_own_sales", label: "View own sales (server)" },
    ] },
  ] },
  { group: "Stock", tabs: [
    { tab: "Shelf", permissions: [{ key: "view_stock", label: "View stock (server)" }] },
    { tab: "Again", permissions: [{ key: "view_stock", label: "Duplicate (server)" }] },
    { tab: "Empty", permissions: [] },
  ] },
  { group: "EmptyGroup", tabs: [] },
];

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
const tick = () => new Promise((r) => setImmediate(r));
async function fire(el, type, ev) {
  for (const fn of (el.handlers[type] || [])) await fn(ev || {});
}

function build(opts) {
  let permBoxes = [];
  const elements = {};
  const reg = { n: 0 };
  const calls = { update: [], create: [] };
  const lastButtons = { edit: [], delete: [] };
  const canConfigure = opts.canConfigure !== false;

  function makeEl(id) {
    const el = {
      id: id, style: {}, dataset: {}, value: "", disabled: false, textContent: "",
      _html: "", handlers: {}, parentNode: null, nextSibling: null,
      classList: {
        _s: new Set(),
        add(c) { this._s.add(c); },
        remove(c) { this._s.delete(c); },
        contains(c) { return this._s.has(c); },
      },
      addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); },
      remove() { this.removed = true; },
      closest() { return null; },
      querySelectorAll() { return []; },
    };
    Object.defineProperty(el, "innerHTML", {
      get() { return this._html !== "" ? this._html : esc(this.textContent); },
      set(v) {
        this._html = String(v);
        if (this.id === "role-permissions-list") {
          permBoxes = [];
          const re = /<input type="checkbox" value="([^"]+)"\s*(checked)?/g;
          let m;
          while ((m = re.exec(this._html))) permBoxes.push({ value: m[1], checked: !!m[2], disabled: false });
        }
      },
    });
    return el;
  }
  function getEl(id) {
    if (!elements[id]) elements[id] = makeEl(id);
    return elements[id];
  }

  const tbody = getEl("roles-tbody");
  const table = makeEl("table");
  table.parentNode = { inserted: [], insertBefore(n) { this.inserted.push(n); } };
  tbody.closest = (sel) => (sel === "table" ? table : null);
  tbody.querySelectorAll = (sel) => {
    const attr = sel === "[data-edit]" ? "edit" : sel === "[data-delete]" ? "delete" : null;
    if (!attr) return [];
    const re = new RegExp("data-" + attr + "=\"([^\"]+)\"", "g");
    const out = [];
    let m;
    while ((m = re.exec(tbody._html))) {
      const b = makeEl("btn");
      b.dataset[attr] = m[1];
      out.push(b);
    }
    lastButtons[attr] = out;
    return out;
  };

  const document = {
    getElementById(id) {
      if (id === "roles-name-note") return table.parentNode.inserted.find((n) => n.id === id) || null;
      return getEl(id);
    },
    createElement() { return makeEl("created"); },
    querySelectorAll(sel) {
      if (sel === "#role-permissions-list input") return permBoxes;
      if (sel === "#role-permissions-list input:checked") return permBoxes.filter((b) => b.checked);
      return [];
    },
  };

  const Api = {
    async listRoles() { return JSON.parse(JSON.stringify(ROLES)); },
    async updateRole(name, body) { calls.update.push({ name: name, body: body }); return {}; },
    async createRole(body) { calls.create.push(body); return {}; },
    async deleteRole() {},
    async getPermissionRegistry() { reg.n++; return opts.registryFn(); },
  };
  const ctx = vm.createContext({
    document: document,
    Api: Api,
    Auth: { requireAuth() {}, logout() {} },
    Perms: { can(a) { return a === "configure_system" ? canConfigure : true; }, applyNav() {} },
    PERMISSION_LABELS: {},
    showToast() {},
    showConfirmModal: async () => true,
    console: console,
  });
  return { ctx: ctx, getEl: getEl, boxes: () => permBoxes, calls: calls, lastButtons: lastButtons, reg: reg };
}

async function start(opts) {
  const h = build(opts);
  vm.runInContext(SRC, h.ctx, { filename: "roles.js" });
  await tick();
  await tick();
  return h;
}

async function openEdit(h, name) {
  const b = h.lastButtons.edit.find((x) => x.dataset.edit === name);
  if (!b) throw new Error("no Edit button for " + name);
  await fire(b, "click");
}

function flatChecks(prefix, h) {
  const html = h.getEl("role-permissions-list")._html;
  const boxes = h.boxes();
  const values = boxes.map((b) => b.value);
  const legacy = boxes.find((b) => b.value === "legacy_thing");
  const sale = boxes.find((b) => b.value === "process_sale");
  check(prefix + "1 the old flat list is drawn (no group headings)", !html.includes("grid-column:1 / -1"));
  check(prefix + "2 the flat list has many boxes", values.length > 30);
  check(prefix + "3 an owner-only key is still in the flat list", values.includes("remove_super_admin"));
  check(prefix + "4 a held permission outside the fixed list is kept and ticked", !!legacy && legacy.checked === true);
  check(prefix + "5 process_sale is ticked", !!sale && sale.checked === true);
}

async function scenarioOk() {
  const h = await start({ registryFn: async () => JSON.parse(JSON.stringify(REGISTRY)) });
  check("A1 the registry is asked for once", h.reg.n === 1);

  await openEdit(h, "cashier");
  const html = h.getEl("role-permissions-list")._html;
  const values = h.boxes().map((b) => b.value).sort();
  const box = (k) => h.boxes().find((b) => b.value === k);
  check("B1 group headings are shown", html.includes(">Sales</div>") && html.includes(">Stock</div>"));
  check("B2 tab headings are shown", html.includes(">POS</div>") && html.includes(">Shelf</div>"));
  check("B3 headings span the full width", html.includes("grid-column:1 / -1"));
  check("B4 groups keep the server order", html.indexOf(">Sales</div>") < html.indexOf(">Stock</div>"));
  check("B5 the server names are shown", html.includes("Process sales (server)") && html.includes("View stock (server)"));
  check("B6 an empty tab and an empty group are not drawn", !html.includes("Empty"));
  check("B7 a key listed twice is drawn once", h.boxes().filter((b) => b.value === "view_stock").length === 1 && !html.includes("Duplicate") && !html.includes("Again"));
  check("B8 boxes are the registry keys plus the held extra key",
    JSON.stringify(values) === JSON.stringify(["legacy_thing", "process_sale", "view_own_sales", "view_stock"]));
  check("C1 held keys are ticked, others not",
    box("process_sale").checked === true && box("legacy_thing").checked === true &&
    box("view_stock").checked === false && box("view_own_sales").checked === false);
  check("C2 all boxes are enabled", h.boxes().every((b) => !b.disabled));
  check("D1 a held key outside the registry goes under Other",
    html.includes(">Other</div>") && html.includes("Legacy Thing") && html.indexOf(">Other</div>") > html.indexOf(">Stock</div>"));

  box("view_stock").checked = true;
  await fire(h.getEl("role-modal-save"), "click");
  const u = h.calls.update[0];
  const sent = u ? u.body.permissions.slice().sort() : [];
  check("E1 save sends the ticked keys, including the extra held key",
    !!u && u.name === "cashier" && JSON.stringify(sent) === JSON.stringify(["legacy_thing", "process_sale", "view_stock"]));
  await tick();

  await openEdit(h, "super_admin");
  check("F1 Super Admin boxes are read-only in the grouped list", h.boxes().length > 0 && h.boxes().every((b) => b.disabled === true));
  check("F2 the hint says the permissions are fixed", h.getEl("role-modal-hint").textContent.includes("fixed"));
  await fire(h.getEl("role-modal-save"), "click");
  const u2 = h.calls.update[h.calls.update.length - 1];
  check("F3 only display_name is sent for super_admin", u2.name === "super_admin" && !("permissions" in u2.body));
  await tick();

  await fire(h.getEl("new-role-btn"), "click");
  const html2 = h.getEl("role-permissions-list")._html;
  check("G1 a new role opens grouped, with 3 boxes, none ticked, none read-only, no Other",
    h.boxes().length === 3 && h.boxes().every((b) => !b.checked && !b.disabled) && !html2.includes(">Other</div>"));
}

async function scenarioFail() {
  const h = await start({ registryFn: async () => { throw new Error("403 Forbidden"); } });
  check("H0 the registry was asked for once and the page still loaded", h.reg.n === 1 && h.lastButtons.edit.length === 3);
  await openEdit(h, "cashier");
  flatChecks("H", h);
  await fire(h.getEl("role-modal-save"), "click");
  const u = h.calls.update[0];
  check("H6 save still works with the flat list",
    !!u && u.body.permissions.includes("process_sale") && u.body.permissions.includes("legacy_thing"));
}

async function scenarioNotAList() {
  const h = await start({ registryFn: async () => ({ detail: "not a list" }) });
  await openEdit(h, "cashier");
  flatChecks("I", h);
}

async function scenarioNoKeys() {
  const h = await start({ registryFn: async () => [{ group: "G", tabs: [{ tab: "T", permissions: [] }] }] });
  await openEdit(h, "cashier");
  flatChecks("J", h);
}

async function scenarioNoConfigure() {
  const h = await start({ canConfigure: false, registryFn: async () => JSON.parse(JSON.stringify(REGISTRY)) });
  check("K0 the registry is not asked for without configure_system", h.reg.n === 0);
  await openEdit(h, "cashier");
  flatChecks("K", h);
}

async function main() {
  await scenarioOk();
  await scenarioFail();
  await scenarioNotAList();
  await scenarioNoKeys();
  await scenarioNoConfigure();
}

main().then(() => {
  console.log(failures ? failures + " check(s) FAILED" : "All checks passed");
  process.exit(failures ? 1 : 0);
}).catch((e) => {
  console.log("FAIL unexpected error: " + ((e && e.stack) || e));
  process.exit(1);
});