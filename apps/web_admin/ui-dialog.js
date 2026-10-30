/* ui-dialog.js
 * Nick Cosmetics - one dialog style for the whole web admin.
 * Replaces the browser's alert / confirm / prompt boxes.
 *
 *   await uiAlert("Saved.");
 *   if (await uiConfirm("Remove this item?")) { ... }
 *   const name = await uiPrompt("Enter a name", "default");   // null if cancelled
 *
 * Options (optional 2nd argument, or a plain string to set the title):
 *   { title, okText, cancelText, danger }
 * Safety net: any leftover alert("...") call is also shown with this style.
 */
(function () {
  "use strict";
  if (window.uiConfirm) return;

  var CSS = [
    ".ud-overlay{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(4,9,20,.68);backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px);animation:ud-fade .14s ease-out}",
    ".ud-card{box-sizing:border-box;width:min(440px,100%);max-height:calc(100vh - 32px);overflow:auto;background:#111b30;border:1px solid #1f2b45;border-radius:16px;padding:24px;box-shadow:0 24px 60px rgba(0,0,0,.55);color:#e8edf7;font-family:inherit;animation:ud-pop .16s ease-out}",
    ".ud-title{margin:0 0 6px;font-size:18px;font-weight:600;line-height:1.3;color:#f1f5fb}",
    ".ud-msg{margin:0 0 20px;font-size:14px;line-height:1.55;color:#9aa6bd;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}",
    ".ud-input{box-sizing:border-box;width:100%;height:44px;margin:0 0 20px;padding:0 14px;background:#0c1527;border:1px solid #22304d;border-radius:10px;color:#e8edf7;font:inherit;font-size:14px;outline:none}",
    ".ud-input:focus{border-color:#4a86ff;box-shadow:0 0 0 3px rgba(74,134,255,.2)}",
    ".ud-actions{display:flex;gap:12px}",
    ".ud-btn{flex:1;height:44px;border-radius:10px;border:1px solid transparent;font:inherit;font-size:15px;font-weight:600;cursor:pointer;transition:filter .12s,background .12s}",
    ".ud-btn:focus-visible{outline:2px solid #fff;outline-offset:2px}",
    ".ud-cancel{background:#0e1729;border-color:#22304d;color:#e8edf7}",
    ".ud-cancel:hover{background:#15213a}",
    ".ud-ok{background:#4a86ff;color:#fff}",
    ".ud-ok:hover{filter:brightness(1.1)}",
    ".ud-ok.ud-danger{background:#e5484d}",
    "@keyframes ud-fade{from{opacity:0}to{opacity:1}}",
    "@keyframes ud-pop{from{opacity:0;transform:translateY(6px) scale(.98)}to{opacity:1;transform:none}}"
  ].join("\n");

  var DANGER_WORDS = /\b(delete|remove|cancel|void|discard|clear|reject|wipe|erase)\b/i;
  var queue = Promise.resolve();

  function ready() {
    if (document.body) return Promise.resolve();
    return new Promise(function (res) {
      document.addEventListener("DOMContentLoaded", res, { once: true });
    });
  }

  function ensureStyle() {
    if (document.getElementById("ud-style")) return;
    var s = document.createElement("style");
    s.id = "ud-style";
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function open(cfg) {
    var job = function () {
      return ready().then(function () {
        ensureStyle();
        return new Promise(function (resolve) {
          var prevFocus = document.activeElement;
          var overlay = el("div", "ud-overlay");
          var card = el("div", "ud-card");
          card.setAttribute("role", "dialog");
          card.setAttribute("aria-modal", "true");
          var titleEl = el("h3", "ud-title", cfg.title);
          var msgEl = el("p", "ud-msg", cfg.message);
          card.appendChild(titleEl);
          card.appendChild(msgEl);
          card.setAttribute("aria-labelledby", "ud-title-" + Date.now());
          titleEl.id = card.getAttribute("aria-labelledby");

          var input = null;
          if (cfg.type === "prompt") {
            input = el("input", "ud-input");
            input.type = "text";
            input.value = cfg.defaultValue == null ? "" : String(cfg.defaultValue);
            card.appendChild(input);
          }

          var actions = el("div", "ud-actions");
          var cancelBtn = null;
          if (cfg.type !== "alert") {
            cancelBtn = el("button", "ud-btn ud-cancel", cfg.cancelText);
            cancelBtn.type = "button";
            actions.appendChild(cancelBtn);
          }
          var okBtn = el("button", "ud-btn ud-ok" + (cfg.danger ? " ud-danger" : ""), cfg.okText);
          okBtn.type = "button";
          actions.appendChild(okBtn);
          card.appendChild(actions);
          overlay.appendChild(card);
          document.body.appendChild(overlay);

          var done = false;
          function close(value) {
            if (done) return;
            done = true;
            document.removeEventListener("keydown", onKey, true);
            overlay.remove();
            try { if (prevFocus && prevFocus.focus) prevFocus.focus(); } catch (e) {}
            resolve(value);
          }
          function accept() {
            if (cfg.type === "confirm") close(true);
            else if (cfg.type === "prompt") close(input.value);
            else close(undefined);
          }
          function dismiss() {
            if (cfg.type === "confirm") close(false);
            else if (cfg.type === "prompt") close(null);
            else close(undefined);
          }
          function onKey(e) {
            if (e.key === "Escape") {
              e.preventDefault(); e.stopPropagation(); dismiss();
            } else if (e.key === "Enter") {
              if (document.activeElement === cancelBtn) return; // let the button click fire
              e.preventDefault(); e.stopPropagation(); accept();
            } else if (e.key === "Tab") {
              var f = Array.prototype.slice.call(card.querySelectorAll("input,button"));
              var first = f[0], last = f[f.length - 1];
              if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
              else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
            }
          }
          document.addEventListener("keydown", onKey, true);
          okBtn.addEventListener("click", accept);
          if (cancelBtn) cancelBtn.addEventListener("click", dismiss);
          overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) dismiss(); });

          if (input) { input.focus(); input.select(); } else { okBtn.focus(); }
        });
      });
    };
    var p = queue.then(job);
    queue = p.catch(function () {});
    return p;
  }

  function opts(o, defaults) {
    var out = {};
    for (var k in defaults) out[k] = defaults[k];
    if (typeof o === "string") out.title = o;
    else if (o && typeof o === "object") for (var j in o) if (o[j] !== undefined) out[j] = o[j];
    return out;
  }

  function uiAlert(message, o) {
    var c = opts(o, { type: "alert", title: "Notice", okText: "OK" });
    c.message = message == null ? "" : String(message);
    return open(c);
  }

  function uiConfirm(message, o) {
    var c = opts(o, { type: "confirm", title: "Please confirm", okText: "OK", cancelText: "Cancel" });
    c.message = message == null ? "" : String(message);
    if (c.danger === undefined) c.danger = DANGER_WORDS.test(c.message);
    return open(c);
  }

  function uiPrompt(message, defaultValue, o) {
    var c = opts(o, { type: "prompt", title: "Enter a value", okText: "OK", cancelText: "Cancel" });
    c.message = message == null ? "" : String(message);
    c.defaultValue = defaultValue;
    return open(c);
  }

  window.uiAlert = uiAlert;
  window.uiConfirm = uiConfirm;
  window.uiPrompt = uiPrompt;
  window.uiDialog = { alert: uiAlert, confirm: uiConfirm, prompt: uiPrompt };

  // Safety net: leftover alert() calls still get the custom look (they just don't block).
  window.nativeAlert = window.alert;
  window.alert = function (m) { uiAlert(m); };
})();
