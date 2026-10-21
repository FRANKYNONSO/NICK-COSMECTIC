Auth.requireAuth();
Perms.applyNav();
Perms.guardPage('configure_system');
const currentUser = Auth.getUser();
const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier' };
document.getElementById('current-user-name').textContent = currentUser?.full_name || 'Signed in';
document.getElementById('current-user-role').textContent = roleLabels[currentUser?.role] || currentUser?.role || '';
document.getElementById('logout-link').addEventListener('click', (e) => { e.preventDefault(); Auth.logout(); });

let toastTimer = null;
function showToast(message, isError = false) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}

function renderLogoPreview(logoUrl) {
  const el = document.getElementById('logo-preview');
  if (logoUrl) {
    el.innerHTML = `<img src="${logoUrl}" alt="Store logo" style="width:100%;height:100%;object-fit:contain;" />`;
  } else {
    el.textContent = 'No logo';
  }
}

async function loadSettings() {
  try {
    const settings = await Api.getStoreSettings();
    document.getElementById('store-name').value = settings.store_name || '';
    document.getElementById('store-email').value = settings.store_email || '';
    document.getElementById('website-url').value = settings.website_url || '';
    document.getElementById('website-qr-caption').value = settings.website_qr_caption || '';
    renderLogoPreview(settings.logo_url);
    StoreBranding.save({
      name: settings.store_name,
      email: settings.store_email || '',
      logoUrl: settings.logo_url || '',
      websiteUrl: settings.website_url || '',
      websiteQrCaption: settings.website_qr_caption || '',
    });
  } catch (err) {
    document.getElementById('load-error').textContent = err.message;
    document.getElementById('load-error').classList.add('visible');
  }
}

document.getElementById('logo-upload-btn').addEventListener('click', () => {
  document.getElementById('logo-file').click();
});

document.getElementById('logo-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const updated = await Api.uploadStoreLogo(file);
    renderLogoPreview(updated.logo_url);
    StoreBranding.save({ logoUrl: updated.logo_url || '' });
    showToast('Logo uploaded — it will now show on every printed receipt. You can replace it again any time.');
  } catch (err) {
    showToast(err.message, true);
  } finally {
    e.target.value = '';
  }
});

document.getElementById('settings-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const saveBtn = document.getElementById('settings-save');
  saveBtn.disabled = true;
  try {
    const updated = await Api.updateStoreSettings({
      store_name: document.getElementById('store-name').value.trim(),
      store_email: document.getElementById('store-email').value.trim() || null,
      website_url: document.getElementById('website-url').value.trim() || null,
      website_qr_caption: document.getElementById('website-qr-caption').value.trim() || null,
    });
    StoreBranding.save({
      name: updated.store_name,
      email: updated.store_email || '',
      logoUrl: updated.logo_url || '',
      websiteUrl: updated.website_url || '',
      websiteQrCaption: updated.website_qr_caption || '',
    });
    showToast('Settings saved.');
  } catch (err) {
    showToast(err.message, true);
  } finally {
    saveBtn.disabled = false;
  }
});

loadSettings();
