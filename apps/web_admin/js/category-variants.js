// Product Categories -- was "Categories & Variants" (update spec 3,
// Section 6): the variant-type/option configuration system was
// retired along with the variants concept it fed (see inventory.js),
// so this page is now plain category management, plus assigning a
// product to a brand and more than one category.
//
// Product Templates (Item 8) removed per follow-up request: this page
// no longer offers a name+category+size catalog separate from live
// products -- "Assign categories to a product" is now the only way to
// attach a category, and it now also attaches a brand.

Auth.requireAuth();
Perms.guardPage('manage_products');
Perms.applyNav();

const roleLabels = { super_admin: 'Super admin', branch_manager: 'Branch manager', staff: 'Cashier', store_assistant: 'Store assistant' }; // PATCHP3C-ROLE-LABEL
const currentUser = Auth.getUser();
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

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// -------------------------------------------------------------- Categories

async function loadCategories() {
  const tbody = document.getElementById('categories-tbody');
  const table = document.getElementById('categories-table');
  const emptyState = document.getElementById('empty-state');
  emptyState.style.display = 'none';
  table.style.display = '';
  tbody.innerHTML = '<tr class="loading-row"><td colspan="3">Loading categories…</td></tr>';

  try {
    const categories = await Api.listCategories();
    if (!categories.length) {
      table.style.display = 'none';
      emptyState.style.display = 'block';
      return;
    }
    tbody.innerHTML = categories.map((c) => `
      <tr>
        <td><strong>${escapeHtml(c.name)}</strong></td>
        <td>${c.product_count ?? 0}</td>
        <td>
          <div class="row-actions-links">
            <button class="action-link action-link-edit" data-edit="${c.id}">Edit</button>
            <button class="action-link action-link-delete" data-delete="${c.id}">Delete</button>
          </div>
        </td>
      </tr>`).join('');

    categories.forEach((c) => {
      document.querySelector(`[data-edit="${c.id}"]`)?.addEventListener('click', () => openEditCategoryModal(c));
      document.querySelector(`[data-delete="${c.id}"]`)?.addEventListener('click', () => deleteCategory(c));
    });
  } catch (err) {
    tbody.innerHTML = '';
    showToast(err.message, true);
  }
}

// -------------------------------------------------- Edit Category modal
//
// Replaces the old rename-only prompt() flow. Renaming still uses
// Api.renameCategory under the hood, but the modal also lists every
// product currently filed under this category (image + name), each
// editable in place (name and/or photo) via the same
// Api.updateProduct / Api.uploadProductImage calls Inventory uses.
// Photos are only ever shown inside this modal -- never on the outer
// categories table.

const editCategoryBackdrop = document.getElementById('edit-category-modal-backdrop');
const editCategoryNameInput = document.getElementById('edit-category-name');
const editCategoryProductsList = document.getElementById('edit-category-products-list');
const editCategoryError = document.getElementById('edit-category-error');
let activeEditCategory = null;

async function openEditCategoryModal(c) {
  activeEditCategory = c;
  editCategoryNameInput.value = c.name;
  editCategoryError.classList.remove('visible');
  editCategoryProductsList.innerHTML = '<p style="color:var(--ink-faint);font-size:12.5px;">Loading products…</p>';
  editCategoryBackdrop.classList.add('visible');
  try {
    const products = await Api.listProducts({ categoryId: c.id });
    renderEditCategoryProducts(products);
  } catch (err) {
    editCategoryProductsList.innerHTML = '';
    showToast(err.message, true);
  }
}

function closeEditCategoryModal() {
  editCategoryBackdrop.classList.remove('visible');
  activeEditCategory = null;
}

document.getElementById('edit-category-cancel').addEventListener('click', closeEditCategoryModal);
editCategoryBackdrop.addEventListener('click', (e) => { if (e.target === editCategoryBackdrop) closeEditCategoryModal(); });

document.getElementById('edit-category-name-save').addEventListener('click', async () => {
  if (!activeEditCategory) return;
  const name = editCategoryNameInput.value.trim();
  editCategoryError.classList.remove('visible');
  if (!name) {
    editCategoryError.textContent = 'Category name cannot be empty.';
    editCategoryError.classList.add('visible');
    return;
  }
  if (name === activeEditCategory.name) return;
  try {
    await Api.renameCategory(activeEditCategory.id, { name });
    showToast('Category renamed.');
    activeEditCategory.name = name;
    await Promise.all([loadCategories(), loadProductPicker(), loadCategorySelect()]);
  } catch (err) {
    editCategoryError.textContent = err.message;
    editCategoryError.classList.add('visible');
  }
});

function renderEditCategoryProducts(products) {
  if (!products.length) {
    editCategoryProductsList.innerHTML = '<p style="color:var(--ink-faint);font-size:12.5px;">No products in this category yet.</p>';
    return;
  }
  editCategoryProductsList.innerHTML = products.map((p) => `
    <div class="ecp-row" data-product-row="${p.id}">
      <div class="ecp-summary">
        <div class="product-cell" style="flex:1;min-width:0;">
          ${p.image_url
            ? `<img class="product-thumb" src="${escapeHtml(p.image_url)}" alt="" />`
            : `<div class="product-thumb placeholder">—</div>`}
          <span class="cell-medium ecp-name-display" style="overflow-wrap:anywhere;">${escapeHtml(p.name)}</span>
        </div>
        <button type="button" class="action-link action-link-edit ecp-edit-btn">Edit</button>
      </div>
      <div class="ecp-edit-panel" style="display:none;">
        <div class="field" style="margin-bottom:10px;">
          <label>Product name</label>
          <input type="text" class="ecp-name-input" value="${escapeHtml(p.name)}" />
        </div>
        <div class="field" style="margin-bottom:10px;">
          <label>Replace photo <span style="font-weight:400;color:var(--ink-faint);">(optional)</span></label>
          <input type="file" accept="image/*" class="ecp-photo-input" />
        </div>
        <div style="display:flex;gap:14px;">
          <button type="button" class="action-link ecp-save-btn" style="color:var(--success);">Save</button>
          <button type="button" class="action-link ecp-cancel-btn" style="color:var(--ink-faint);">Cancel</button>
        </div>
      </div>
    </div>`).join('');
}

editCategoryProductsList.addEventListener('click', (e) => {
  const row = e.target.closest('.ecp-row');
  if (!row) return;
  const productId = row.dataset.productRow;
  const nameDisplay = row.querySelector('.ecp-name-display');
  const nameInput = row.querySelector('.ecp-name-input');
  const photoInput = row.querySelector('.ecp-photo-input');
  const editBtn = row.querySelector('.ecp-edit-btn');
  const saveBtn = row.querySelector('.ecp-save-btn');
  const cancelBtn = row.querySelector('.ecp-cancel-btn');
  const editPanel = row.querySelector('.ecp-edit-panel');

  if (e.target === editBtn) {
    editPanel.style.display = '';
  } else if (e.target === cancelBtn) {
    nameInput.value = nameDisplay.textContent;
    photoInput.value = '';
    editPanel.style.display = 'none';
  } else if (e.target === saveBtn) {
    saveEditCategoryProduct(productId, row);
  }
});

async function saveEditCategoryProduct(productId, row) {
  const nameInput = row.querySelector('.ecp-name-input');
  const nameDisplay = row.querySelector('.ecp-name-display');
  const photoInput = row.querySelector('.ecp-photo-input');
  const newName = nameInput.value.trim();
  if (!newName) {
    showToast('Product name cannot be empty.', true);
    return;
  }
  try {
    if (newName !== nameDisplay.textContent) {
      await Api.updateProduct(productId, { name: newName });
    }
    const file = photoInput.files[0];
    if (file) {
      await Api.uploadProductImage(productId, file);
    }
    showToast('Product updated.');
    const products = await Api.listProducts({ categoryId: activeEditCategory.id });
    renderEditCategoryProducts(products);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function deleteCategory(c) {
  if (!(await showConfirmModal(`Products in this category will need to be recategorized.`, { title: `Delete "${c.name}"?`, confirmLabel: 'Delete' }))) return;
  try {
    await Api.deleteCategory(c.id);
    showToast('Category deleted.');
    await loadCategories();
    await loadProductPicker();
    await loadCategorySelect();
  } catch (err) {
    showToast(err.message, true);
  }
}

document.getElementById('product-category-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const productId = document.getElementById('pc-product').value;
  const brandId = document.getElementById('pc-brand').value;
  const categorySelect = document.getElementById('pc-category-name');
  const categoryId = categorySelect.value;
  const categoryName = categorySelect.selectedOptions[0]?.textContent || '';
  if (!productId) {
    showToast('Pick a product first.', true);
    return;
  }
  if (!categoryId) {
    showToast('Pick a category first.', true);
    return;
  }
  try {
    // "Also list under" (extra categories) removed from this form
    // (September 2026 change spec section 5) -- extra_category_ids is
    // deliberately left out of this update entirely (not sent as an
    // empty list) so a product that already has extra categories from
    // before this change keeps them; this form just can't touch that
    // field anymore.
    await Api.updateProduct(productId, {
      category_id: categoryId,
      brand_id: brandId || null,
    });
    showToast(`"${categoryName}" saved for this product.`);
    document.getElementById('product-category-form').reset();
    await Promise.all([loadCategories(), loadProductPicker(), loadCategorySelect(), loadBrandPicker()]);
  } catch (err) {
    showToast(err.message, true);
  }
});

// Lets a brand-new product be created right from this page, same
// quick-add pattern used on the Warehouse page, so assigning a
// category doesn't require a detour to Inventory first.
//
// Upgraded from a plain prompt() flow to a real modal (follow-up
// request) so it can also collect a barcode -- via the same
// BarcodeDetector-based Scan button as Inventory's own "Add product"
// modal (see inventory.js) -- and a product photo, uploaded the same
// way: create the product first, then a follow-up
// Api.uploadProductImage(productId, file) call once we have an id.

const qapModalBackdrop = document.getElementById('qap-modal-backdrop');
const qapForm = document.getElementById('qap-form');
const qapModalError = document.getElementById('qap-modal-error');
const qapImageFileInput = document.getElementById('qap-image-file');

function openQuickAddProductModal() {
  qapForm.reset();
  qapModalError.classList.remove('visible');
  qapModalBackdrop.classList.add('visible');
}

function closeQuickAddProductModal() {
  stopQapBarcodeScan();
  qapModalBackdrop.classList.remove('visible');
}

document.getElementById('pc-quick-add-product').addEventListener('click', openQuickAddProductModal);
document.getElementById('qap-cancel').addEventListener('click', closeQuickAddProductModal);
qapModalBackdrop.addEventListener('click', (e) => {
  if (e.target === qapModalBackdrop) closeQuickAddProductModal();
});

// -------------------------------------------------------- Barcode scan
//
// Identical to Inventory's implementation (js/inventory.js) -- same
// native BarcodeDetector API, same fallback message where it's not
// supported, just wired to this modal's own element ids so both
// modals can exist on their respective pages without id collisions.

let qapBarcodeScanStream = null;
let qapBarcodeScanRAF = null;

function stopQapBarcodeScan() {
  if (qapBarcodeScanRAF) cancelAnimationFrame(qapBarcodeScanRAF);
  qapBarcodeScanRAF = null;
  if (qapBarcodeScanStream) {
    qapBarcodeScanStream.getTracks().forEach((t) => t.stop());
    qapBarcodeScanStream = null;
  }
  document.getElementById('qap-barcode-scan-video').style.display = 'none';
  document.getElementById('qap-barcode-scan-hint').style.display = 'none';
  document.getElementById('qap-barcode-scan-btn').textContent = 'ðŸ“· Scan';
}

document.getElementById('qap-barcode-scan-btn').addEventListener('click', async () => {
  if (qapBarcodeScanStream) {
    stopQapBarcodeScan();
    return;
  }
  const hint = document.getElementById('qap-barcode-scan-hint');
  if (!('BarcodeDetector' in window)) {
    hint.textContent = "Camera barcode scanning isn't supported on this browser -- type the barcode in manually instead.";
    hint.style.display = 'block';
    return;
  }
  try {
    qapBarcodeScanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    const video = document.getElementById('qap-barcode-scan-video');
    video.srcObject = qapBarcodeScanStream;
    video.style.display = 'block';
    await video.play();
    document.getElementById('qap-barcode-scan-btn').textContent = 'Stop scanning';
    hint.textContent = 'Point the camera at the barcode…';
    hint.style.display = 'block';

    const detector = new BarcodeDetector();
    const scanFrame = async () => {
      if (!qapBarcodeScanStream) return;
      try {
        const codes = await detector.detect(video);
        if (codes.length) {
          document.getElementById('qap-barcode').value = codes[0].rawValue;
          showToast('Barcode captured.');
          stopQapBarcodeScan();
          return;
        }
      } catch (err) { /* keep trying on transient decode errors */ }
      qapBarcodeScanRAF = requestAnimationFrame(scanFrame);
    };
    qapBarcodeScanRAF = requestAnimationFrame(scanFrame);
  } catch (err) {
    hint.textContent = `Couldn't access the camera: ${err.message}`;
    hint.style.display = 'block';
  }
});

qapForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const saveBtn = document.getElementById('qap-save');
  saveBtn.disabled = true;
  qapModalError.classList.remove('visible');

  const name = document.getElementById('qap-name').value.trim();
  const barcode = document.getElementById('qap-barcode').value.trim() || null;

  if (!name) {
    qapModalError.textContent = 'Enter a product name.';
    qapModalError.classList.add('visible');
    saveBtn.disabled = false;
    return;
  }

  try {
    const product = await Api.createProduct({
      name,
      barcode,
    });

    // Image upload needs a product id, so it happens as a follow-up
    // call after create succeeds -- same pattern as Inventory's own
    // Add product modal (see /catalog/products/{id}/images).
    const file = qapImageFileInput.files[0];
    if (file) {
      try {
        await Api.uploadProductImage(product.id, file);
      } catch (err) {
        showToast(`Product saved, but the photo upload failed: ${err.message}`, true);
      }
    }

    await loadProductPicker();
    document.getElementById('pc-product').value = product.id;
    closeQuickAddProductModal();
    showToast(`Product "${name}" added -- now give it a category.`);
  } catch (err) {
    qapModalError.textContent = err.message;
    qapModalError.classList.add('visible');
  } finally {
    saveBtn.disabled = false;
  }
});

// Same quick-add pattern for a brand that doesn't exist yet -- picks it
// straight into the form so the product can be saved with it right away.
document.getElementById('pc-quick-add-brand').addEventListener('click', async () => {
  const name = await showPromptModal('', { title: 'New brand name', confirmLabel: 'Add' });
  if (!name || !name.trim()) return;
  try {
    const brand = await Api.createBrand({ name: name.trim() });
    await loadBrandPicker();
    document.getElementById('pc-brand').value = brand.id;
    showToast(`Brand "${name.trim()}" added.`);
  } catch (err) {
    showToast(err.message, true);
  }
});

// Same quick-add pattern as Brand -- creates the category immediately
// (reusing an existing one of the same name instead of duplicating it)
// and selects it straight into the Primary category dropdown.
document.getElementById('pc-quick-add-category').addEventListener('click', async () => {
  const name = await showPromptModal('', { title: 'New category name', confirmLabel: 'Add' });
  if (!name || !name.trim()) return;
  try {
    const categories = await Api.listCategories();
    let category = categories.find((c) => c.name.toLowerCase() === name.trim().toLowerCase());
    if (category) {
      showToast(`Category "${category.name}" already exists -- picking it.`);
    } else {
      category = await Api.createCategory({ name: name.trim() });
      showToast(`Category "${name.trim()}" added.`);
    }
    await loadCategorySelect();
    document.getElementById('pc-category-name').value = category.id;
  } catch (err) {
    showToast(err.message, true);
  }
});

// The product picker shows each product's current category (if any)
// so it's obvious at a glance which products still need one.
let productCache = [];
async function loadProductPicker() {
  const select = document.getElementById('pc-product');
  const previousValue = select.value;
  try {
    const [products, categories] = await Promise.all([Api.listProducts({}), Api.listCategories()]);
    productCache = products;
    const categoryNameById = new Map(categories.map((c) => [c.id, c.name]));
    select.innerHTML = products.map((p) => {
      const currentCategory = p.category_id ? categoryNameById.get(p.category_id) : null;
      const sizePart = p.size ? ` — ${p.size}` : '';
      const label = currentCategory ? `${p.name}${sizePart} (currently: ${currentCategory})` : `${p.name}${sizePart} (no category yet)`;
      return `<option value="${p.id}">${escapeHtml(label)}</option>`;
    }).join('');
    if (previousValue && products.some((p) => p.id === previousValue)) select.value = previousValue;
  } catch (err) {
    showToast(err.message, true);
  }
}

// Primary category dropdown for the "Assign categories to a product"
// form -- matches the Brand/Product select pattern exactly, so picking
// a category works the same way as picking those two.
async function loadCategorySelect() {
  const select = document.getElementById('pc-category-name');
  const previousValue = select.value;
  try {
    const categories = await Api.listCategories();
    select.innerHTML = categories.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
    if (previousValue && categories.some((c) => c.id === previousValue)) select.value = previousValue;
  } catch (err) {
    showToast(err.message, true);
  }
}

// Brand picker for the "Assign categories to a product" form -- kept
// separate from category since a product only ever has one brand
// (no "extra brands" concept, unlike extra categories).
async function loadBrandPicker() {
  const select = document.getElementById('pc-brand');
  const previousValue = select.value;
  try {
    const brands = await Api.listBrands();
    select.innerHTML = '<option value="">No brand</option>' +
      brands.map((b) => `<option value="${b.id}">${escapeHtml(b.name)}</option>`).join('');
    if (previousValue && brands.some((b) => b.id === previousValue)) select.value = previousValue;
  } catch (err) {
    showToast(err.message, true);
  }
}

loadCategories();
loadProductPicker();
loadCategorySelect();
loadBrandPicker();