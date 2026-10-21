// Shared in-app text-input modal -- replaces window.prompt() native
// browser popups. Self-injecting: just add
// <script src="js/prompt-modal.js"></script> to a page and call
// showPromptModal(message, { title, defaultValue, confirmLabel, placeholder })
// -- resolves to the typed string, or null if cancelled (same contract
// as window.prompt()). No markup needed in that page's HTML.
(function () {
  if (document.getElementById('prompt-modal-backdrop')) return;
  const wrapper = document.createElement('div');
  wrapper.innerHTML = `
    <div class="modal-backdrop" id="prompt-modal-backdrop">
      <div class="modal" style="max-width:420px;">
        <h3 id="prompt-modal-title">Enter a value</h3>
        <p class="modal-sub" id="prompt-modal-message"></p>
        <div class="field">
          <input type="text" id="prompt-modal-input" autocomplete="off" />
        </div>
        <div class="modal-actions" style="display:flex;gap:10px;margin-top:16px;">
          <button type="button" class="btn btn-ghost" id="prompt-modal-cancel" style="flex:1;">Cancel</button>
          <button type="button" class="btn btn-primary" id="prompt-modal-confirm" style="flex:1;">OK</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrapper.firstElementChild);
})();

function showPromptModal(message, { title = 'Enter a value', defaultValue = '', confirmLabel = 'OK', placeholder = '' } = {}) {
  const backdrop = document.getElementById('prompt-modal-backdrop');
  document.getElementById('prompt-modal-title').textContent = title;
  const messageEl = document.getElementById('prompt-modal-message');
  messageEl.textContent = message || '';
  messageEl.style.display = message ? '' : 'none';
  const input = document.getElementById('prompt-modal-input');
  input.value = defaultValue || '';
  input.placeholder = placeholder || '';
  const confirmBtn = document.getElementById('prompt-modal-confirm');
  const cancelBtn = document.getElementById('prompt-modal-cancel');
  confirmBtn.textContent = confirmLabel;
  backdrop.classList.add('visible');
  setTimeout(() => { input.focus(); input.select(); }, 0);
  return new Promise((resolve) => {
    const cleanup = (result) => {
      backdrop.classList.remove('visible');
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
      input.removeEventListener('keydown', onKeydown);
      resolve(result);
    };
    const onConfirm = () => cleanup(input.value);
    const onCancel = () => cleanup(null);
    const onKeydown = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); onConfirm(); }
      else if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
    };
    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
    input.addEventListener('keydown', onKeydown);
  });
}
