// Shared in-app confirm modal -- replaces window.confirm()/confirm()
// native browser popups everywhere except inventory.html (which already
// has its own local copy of this same component). Self-injecting: just
// add <script src="js/confirm-modal.js"></script> to a page and call
// showConfirmModal(message, { title, confirmLabel }) -- no markup needed
// in that page's HTML. Works the same in dev and once this is packaged
// or deployed live, unlike the native browser confirm() bar.
(function () {
  if (document.getElementById('confirm-modal-backdrop')) return;
  const wrapper = document.createElement('div');
  wrapper.innerHTML = `
    <div class="modal-backdrop" id="confirm-modal-backdrop">
      <div class="modal" style="max-width:420px;">
        <h3 id="confirm-modal-title">Are you sure?</h3>
        <p class="modal-sub" id="confirm-modal-message"></p>
        <div class="modal-actions" style="display:flex;gap:10px;margin-top:16px;">
          <button type="button" class="btn btn-ghost" id="confirm-modal-cancel" style="flex:1;">Cancel</button>
          <button type="button" class="btn btn-danger" id="confirm-modal-confirm" style="flex:1;">Confirm</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrapper.firstElementChild);
})();

function showConfirmModal(message, { title = 'Are you sure?', confirmLabel = 'Confirm' } = {}) {
  const backdrop = document.getElementById('confirm-modal-backdrop');
  document.getElementById('confirm-modal-title').textContent = title;
  document.getElementById('confirm-modal-message').textContent = message;
  const confirmBtn = document.getElementById('confirm-modal-confirm');
  const cancelBtn = document.getElementById('confirm-modal-cancel');
  confirmBtn.textContent = confirmLabel;
  backdrop.classList.add('visible');
  return new Promise((resolve) => {
    const cleanup = (result) => {
      backdrop.classList.remove('visible');
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
      resolve(result);
    };
    const onConfirm = () => cleanup(true);
    const onCancel = () => cleanup(false);
    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
  });
}
