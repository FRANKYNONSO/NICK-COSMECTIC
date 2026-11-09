"use strict";
// PATCHR2-ROLES-UI: checks the Roles screen script (web_admin/js/roles.js)
// against a tiny fake page. Run: node check_roles_edit.js
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
  { id: "1", name: "super_admin", display_name: "Super Admin", permissions: ["manage_users", "process_sale"], is_system: true, user_count: 1 },
  { id: "2", name: "branch_manager", display_name: "Branch Manager", permissions: ["view_stock", "process_sale"], is_system: true, user_count: 2 },
  { id: "3", name: "cashier", display_name: "Cashier", permissions: ["process_sale"], is_system: true, user_count: 3 },
  { id: "4", name: "store_assistant", display_name: "Store Assistant", permissions: ["view_stock"], is_system: false, user_count: 0 },
  { id: "5", name: "custom_one", display_name: "Custom One", permissions: ["view_stock"], is_system: false, user_count: 0 },
];

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

let permBoxes = [];
const elements = {};
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
async function fire(el, type, ev) {
  for (const fn of (el.handlers[type] || [])) await fn(ev || {});
}
const tick = () => new Promise((r) => setImmediate(r));

const lastButtons = { edit: [], delete: [] };
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
  // PATCHR2B-CHECKFIX: like a real page, return null for the note until it has been inserted.
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

const calls = { update: [], create: [], del: [] };
const Api = {
  async listRoles() { return JSON.parse(JSON.stringify(ROLES)); },
  async updateRole(name, body) { calls.update.push({ name: name, body: body }); return {}; },
  async createRole(body) { calls.create.push(body); return {}; },
  async deleteRole(name) { calls.del.push(name); },
};
const ctx = vm.createContext({
  document: document,
  Api: Api,
  Auth: { requireAuth() {}, logout() {} },
  Perms: { can() { return true; }, applyNav() {} },
  PERMISSION_LABELS: {},
  showToast() {},
  showConfirmModal: async () => true,
  console: console,
});

async function clickEdit(name) {
  const b = lastButtons.edit.find((x) => x.dataset.edit === name);
  if (!b) throw new Error("no Edit button for " + name);
  await fire(b, "click");
}

async function main() {
  vm.runInContext(SRC, ctx, { filename: "roles.js" });
  await tick();

  const html = tbody._html;
  const editNames = lastButtons.edit.map((b) => b.dataset.edit);
  const delNames = lastButtons.delete.map((b) => b.dataset.delete);
  check("A1 no Locked label is shown", !html.includes("Locked"));
  check("A2 all five roles have an Edit button",
    editNames.length === 5 && ["super_admin", "branch_manager", "cashier", "store_assistant", "custom_one"].every((n) => editNames.includes(n)));
  check("A3 only the custom role has a Delete button", delNames.length === 1 && delNames[0] === "custom_one");

  const inserted = table.parentNode.inserted;
  check("B1 a note about role-name-checked pages is shown",
    inserted.length === 1 && String(inserted[0].textContent).includes("check the role itself"));

  const backdrop = getEl("role-modal-backdrop");
  const save = getEl("role-modal-save");

  await clickEdit("cashier");
  check("C1 Edit opens the modal for the Cashier role",
    backdrop.classList.contains("visible") && getEl("role-display-name").value === "Cashier");
  check("C2 Cashier permission boxes are enabled", permBoxes.length > 0 && permBoxes.every((b) => !b.disabled));
  check("C3 process_sale is ticked", permBoxes.find((b) => b.value === "process_sale").checked === true);
  getEl("role-display-name").value = "Till Operator";
  permBoxes.find((b) => b.value === "view_stock").checked = true;
  await fire(save, "click");
  const u1 = calls.update[0];
  check("C4 save sends name and permissions for staff",
    !!u1 && u1.name === "cashier" && u1.body.display_name === "Till Operator" &&
    u1.body.permissions.includes("process_sale") && u1.body.permissions.includes("view_stock"));
  check("C5 modal closes after saving", !backdrop.classList.contains("visible"));
  await tick();

  await clickEdit("store_assistant");
  check("C6 Store Assistant opens with its boxes enabled",
    getEl("role-display-name").value === "Store Assistant" && permBoxes.every((b) => !b.disabled));
  await fire(getEl("role-modal-cancel"), "click");

  await clickEdit("super_admin");
  check("D1 Super Admin modal opens",
    backdrop.classList.contains("visible") && getEl("role-display-name").value === "Super Admin");
  check("D2 all Super Admin permission boxes are read-only", permBoxes.length > 0 && permBoxes.every((b) => b.disabled === true));
  check("D3 the hint says the permissions are fixed", getEl("role-modal-hint").textContent.includes("fixed"));
  getEl("role-display-name").value = "Owner";
  await fire(save, "click");
  const u2 = calls.update[calls.update.length - 1];
  check("D4 only display_name is sent for super_admin",
    u2.name === "super_admin" && u2.body.display_name === "Owner" && !("permissions" in u2.body));
  await tick();

  await clickEdit("cashier");
  getEl("role-display-name").value = "   ";
  const before = calls.update.length;
  await fire(save, "click");
  check("E1 a blank name is refused in the page",
    calls.update.length === before && getEl("role-modal-error").classList.contains("visible"));
  await fire(getEl("role-modal-cancel"), "click");

  await ctx.deleteRole("cashier");
  check("F1 delete is refused for a system role", calls.del.length === 0);
  await fire(lastButtons.delete[0], "click");
  check("F2 delete works for the custom role", calls.del.length === 1 && calls.del[0] === "custom_one");

  await fire(getEl("new-role-btn"), "click");
  check("G1 a new role opens empty with the Role ID field visible",
    getEl("role-display-name").value === "" && getEl("role-name-field").style.display === "" &&
    permBoxes.every((b) => !b.checked && !b.disabled));
  getEl("role-display-name").value = "Gift Desk";
  await fire(getEl("role-display-name"), "input", { target: { value: "Gift Desk" } });
  check("G2 the Role ID is filled in from the name", getEl("role-name").value === "gift_desk");
  await fire(save, "click");
  check("G3 the new role is created",
    calls.create.length === 1 && calls.create[0].name === "gift_desk" && calls.create[0].display_name === "Gift Desk");
}

main().then(() => {
  console.log(failures ? failures + " check(s) FAILED" : "All checks passed");
  process.exit(failures ? 1 : 0);
}).catch((e) => {
  console.log("FAIL unexpected error: " + ((e && e.stack) || e));
  process.exit(1);
});
