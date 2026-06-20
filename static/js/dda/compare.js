/** Change Detection tab — pick library images and run comparison. */

const compareState = { t1: null, t2: null, pickingSlot: null };

function compareFormatBytes(n) {
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
  return (n / 1024).toFixed(0) + ' KB';
}

function thumbUrlFor(path) {
  return `/api/dda/local/thumb?path=${encodeURIComponent(path)}`;
}

function encodePath(path) {
  return encodeURIComponent(path || '');
}

function decodePath(encoded) {
  try {
    return decodeURIComponent(encoded || '');
  } catch (_) {
    return encoded || '';
  }
}

function ensureDdaState() {
  window.ddaState = window.ddaState || {};
  return window.ddaState;
}

function updateRunButton() {
  const btn = document.getElementById('btn-run-job');
  if (btn) btn.disabled = !(compareState.t1 && compareState.t2);
}

function renderSlotPreview(slotKey, selection) {
  const wrap = document.getElementById(slotKey === 't1' ? 'slot-t1-preview' : 'slot-t2-preview');
  const slot = document.getElementById(slotKey === 't1' ? 'slot-t1' : 'slot-t2');
  if (!wrap || !slot) return;

  if (!selection) {
    wrap.innerHTML = '<p class="dim">No image selected</p>';
    slot.classList.remove('filled');
    return;
  }

  slot.classList.add('filled');
  const thumb = selection.thumbUrl || thumbUrlFor(selection.path);
  wrap.innerHTML = `
    <img class="dda-slot-preview" src="${thumb}" alt="" />
    <div class="dda-slot-meta">${escapeHtml(selection.label || selection.filename)}</div>`;
}

function populateSelects(items) {
  ['select-t1', 'select-t2'].forEach((id) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const current = sel.value;
    const label = id === 'select-t1' ? '— Choose base image —' : '— Choose comparison image —';
    sel.innerHTML = `<option value="">${label}</option>` +
      items.map((img) => `<option value="${encodePath(img.path)}">${escapeHtml(img.breadcrumb || img.nodePath || img.filename)}</option>`).join('');
    if (current) sel.value = current;
  });
}

function setSlot(slotKey, img) {
  if (!img || !img.path) return;
  const item = {
    path: img.path,
    label: img.breadcrumb || img.nodePath || img.filename,
    filename: img.filename,
    thumbUrl: img.thumbUrl || thumbUrlFor(img.path),
  };
  if (slotKey === 't1') {
    compareState.t1 = item;
    const sel = document.getElementById('select-t1');
    if (sel) sel.value = encodePath(item.path);
    renderSlotPreview('t1', item);
  } else {
    compareState.t2 = item;
    const sel = document.getElementById('select-t2');
    if (sel) sel.value = encodePath(item.path);
    renderSlotPreview('t2', item);
  }
  updateRunButton();
  refreshCompareLibrarySelection();
  if (typeof showDdaSuccess === 'function') {
    showDdaSuccess(`${slotKey.toUpperCase()} set: ${item.filename}`);
  }
}

function findLibraryItem(path) {
  const norm = decodePath(path);
  const items = ensureDdaState().libraryItems || [];
  return items.find((i) => i.path === norm) || {
    path: norm,
    label: norm.split('/').pop(),
    filename: norm.split('/').pop(),
    thumbUrl: thumbUrlFor(norm),
  };
}

function refreshCompareLibrarySelection() {
  const grid = document.getElementById('compare-lib-grid');
  if (!grid) return;
  grid.querySelectorAll('.dda-compare-card').forEach((card) => {
    const path = decodePath(card.dataset.imagePath || '');
    card.classList.toggle('selected-t1', !!(compareState.t1 && compareState.t1.path === path));
    card.classList.toggle('selected-t2', !!(compareState.t2 && compareState.t2.path === path));
  });
}

async function loadCompareLibraryGrid() {
  const grid = document.getElementById('compare-lib-grid');
  if (!grid) return;

  grid.innerHTML = '<p class="dim">Loading library images…</p>';
  try {
    const items = await ddaApi('GET', '/api/dda/local/images');
    ensureDdaState().libraryItems = items;
    populateSelects(items);

    if (!items.length) {
      grid.innerHTML = '<p class="dim">No images found. Copy .tif files into <code>library_sources/YEAR/</code> in your project folder, then click <strong>Refresh list</strong>.</p>';
      return;
    }

    grid.innerHTML = items.map((img) => {
      const thumb = img.thumbUrl || thumbUrlFor(img.path);
      const enc = encodePath(img.path);
      const t1Sel = compareState.t1?.path === img.path ? ' selected-t1' : '';
      const t2Sel = compareState.t2?.path === img.path ? ' selected-t2' : '';
      const safeName = img.filename.replace(/</g, '&lt;');
      return `
        <div class="dda-compare-card dda-card-img${t1Sel}${t2Sel}" data-image-path="${enc}" draggable="true">
          ${thumb ? `<img src="${thumb}" alt="" loading="lazy" draggable="false" />` : '<div class="meta">No preview</div>'}
          <div class="meta">
            <span class="dim">${escapeHtml(img.breadcrumb || img.nodePath || '')}</span><br/>
            ${safeName}<br/>
            <span class="dim">${compareFormatBytes(img.fileSizeBytes)}</span>
          </div>
          <div class="dda-compare-assign">
            <button type="button" class="btn btn-secondary btn-sm" data-assign="t1" data-path="${enc}">T1</button>
            <button type="button" class="btn btn-secondary btn-sm" data-assign="t2" data-path="${enc}">T2</button>
          </div>
        </div>`;
    }).join('');

    grid.querySelectorAll('.dda-compare-card').forEach((card) => {
      card.addEventListener('dragstart', (e) => {
        const path = decodePath(card.dataset.imagePath);
        e.dataTransfer.setData('application/x-dda-image-path', path);
        e.dataTransfer.setData('text/plain', path);
      });
    });
  } catch (err) {
    grid.innerHTML = `<p class="dim">Could not load images: ${err.message}</p>`;
    if (typeof showDdaError === 'function') showDdaError(err.message);
  }
}

async function openPicker(slotKey) {
  compareState.pickingSlot = slotKey;
  const modal = document.getElementById('dda-picker-modal');
  const list = document.getElementById('dda-picker-list');
  const title = document.getElementById('dda-picker-title');
  if (!modal || !list) return;

  if (title) {
    title.textContent = slotKey === 't1' ? 'Select Base Image (T1)' : 'Select Comparison Image (T2)';
  }
  list.innerHTML = '<p class="dim">Loading library…</p>';
  modal.classList.remove('hidden');

  let items = ensureDdaState().libraryItems || [];
  try {
    items = await ddaApi('GET', '/api/dda/local/images');
    ensureDdaState().libraryItems = items;
    populateSelects(items);
  } catch (err) {
    list.innerHTML = `<p class="dim">Could not load images: ${err.message}</p>`;
    return;
  }

  if (!items.length) {
    list.innerHTML = '<p class="dim">No images in library. Add files under library_sources/YEAR/ and refresh.</p>';
  } else {
    list.innerHTML = items.map((img) => {
      const enc = encodePath(img.path);
      return `
      <button type="button" class="dda-picker-item" data-path="${enc}">
        <img src="${img.thumbUrl || thumbUrlFor(img.path)}" alt="" loading="lazy" />
        <span>${escapeHtml(img.breadcrumb || img.filename)}</span>
      </button>`;
    }).join('');
    list.querySelectorAll('.dda-picker-item').forEach((btn) => {
      btn.addEventListener('click', () => {
        const slot = compareState.pickingSlot;
        if (!slot) return;
        setSlot(slot, findLibraryItem(btn.dataset.path));
        closePicker();
      });
    });
  }
}

function closePicker() {
  document.getElementById('dda-picker-modal')?.classList.add('hidden');
  compareState.pickingSlot = null;
}

function setupCompareInteractions() {
  const slotsWrap = document.querySelector('.dda-compare-slots');
  if (slotsWrap) {
    slotsWrap.addEventListener('click', (e) => {
      const pickBtn = e.target.closest('.dda-slot-pick');
      if (pickBtn) {
        e.preventDefault();
        openPicker(pickBtn.dataset.pick);
      }
    });
  }

  document.getElementById('select-t1')?.addEventListener('change', (e) => {
    const path = e.target.value;
    if (!path) {
      compareState.t1 = null;
      renderSlotPreview('t1', null);
      updateRunButton();
      refreshCompareLibrarySelection();
      return;
    }
    setSlot('t1', findLibraryItem(path));
  });

  document.getElementById('select-t2')?.addEventListener('change', (e) => {
    const path = e.target.value;
    if (!path) {
      compareState.t2 = null;
      renderSlotPreview('t2', null);
      updateRunButton();
      refreshCompareLibrarySelection();
      return;
    }
    setSlot('t2', findLibraryItem(path));
  });

  const grid = document.getElementById('compare-lib-grid');
  if (grid) {
    grid.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-assign]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      setSlot(btn.dataset.assign, findLibraryItem(btn.dataset.path));
    });
  }

  ['slot-t1', 'slot-t2'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('dragover', (e) => { e.preventDefault(); el.classList.add('drag-over'); });
    el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
    el.addEventListener('drop', (e) => {
      e.preventDefault();
      el.classList.remove('drag-over');
      const path = e.dataTransfer.getData('application/x-dda-image-path')
        || e.dataTransfer.getData('text/plain');
      if (!path) return;
      setSlot(id === 'slot-t1' ? 't1' : 't2', findLibraryItem(path));
    });
  });

  document.getElementById('btn-compare-refresh')?.addEventListener('click', () => loadCompareLibraryGrid());
  document.getElementById('dda-picker-close')?.addEventListener('click', closePicker);
  document.getElementById('dda-picker-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'dda-picker-modal') closePicker();
  });
  document.getElementById('btn-run-job')?.addEventListener('click', runLibraryDetection);

  const notifyCb = document.getElementById('dda-detect-notify');
  const notifyEmail = document.getElementById('dda-detect-notify-email');
  notifyCb?.addEventListener('change', () => {
    if (notifyEmail) notifyEmail.classList.toggle('hidden', !notifyCb.checked);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const picker = document.getElementById('dda-picker-modal');
    if (picker && !picker.classList.contains('hidden')) closePicker();
  });
}

function showDetectResult(data) {
  if (typeof showDdaResult === 'function') showDdaResult(data);
  else if (typeof showDdaError === 'function') showDdaError('Result viewer failed to load.');
}

async function runDetectionWithFallback(form, loadingEl) {
  const hosted = window.ddaState?.localCfg?.isHosted;
  if (hosted) {
    loadingEl.textContent = 'Queuing detection job…';
    try {
      const queued = await ddaApi('POST', '/api/dda/jobs', { body: form });
      return await pollJobUntilDone(queued.jobId, loadingEl);
    } catch (err) {
      const msg = String(err.message || '');
      const useSync = msg.includes('Not Found') || msg.includes('404')
        || msg.includes('503') || msg.includes('409') || msg.includes('busy');
      if (!useSync) throw err;
      loadingEl.textContent = 'Running detection (sync fallback)…';
    }
  }
  return ddaApi('POST', '/api/dda/detect/from-library', { body: form }).then((result) => ({ result, jobId: null }));
}

async function pollJobUntilDone(jobId, loadingEl) {
  const maxAttempts = 600;
  for (let i = 0; i < maxAttempts; i++) {
    const job = await ddaApi('GET', `/api/dda/jobs/${jobId}`);
    const status = job.status;
    if (loadingEl) {
      loadingEl.textContent = `Detection job #${jobId} — ${status}… (${i + 1})`;
    }
    if (status === 'completed') {
      if (job.result) {
        if (typeof window.refreshDdaNotifications === 'function') window.refreshDdaNotifications();
        return { result: job.result, jobId };
      }
      if (job.runId) {
        const data = await ddaApi('GET', `/api/history/${job.runId}`);
        if (typeof window.refreshDdaNotifications === 'function') window.refreshDdaNotifications();
        return { result: data, jobId };
      }
      if (job.resultError) throw new Error(job.resultError);
    }
    if (status === 'failed') throw new Error(job.errorMessage || 'Detection job failed');
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error('Detection timed out. Check Reports tab for job status.');
}

async function runLibraryDetection() {
  if (!compareState.t1 || !compareState.t2) {
    if (typeof showDdaError === 'function') showDdaError('Select both T1 (base) and T2 (comparison) images first.');
    return;
  }
  const btn = document.getElementById('btn-run-job');
  const loading = document.getElementById('dda-detect-loading');
  if (typeof hideDdaError === 'function') hideDdaError();
  btn.disabled = true;
  loading?.classList.remove('hidden');
  loading.textContent = 'Running detection…';

  const form = new FormData();
  form.append('base_path', compareState.t1.path);
  form.append('comparison_path', compareState.t2.path);
  form.append('title', `${compareState.t1.filename} vs ${compareState.t2.filename}`);
  form.append('method', document.getElementById('dda-detect-method')?.value || 'AI-Based Deep Learning');
  form.append('enable_registration', String(document.getElementById('dda-detect-registration')?.checked !== false));
  form.append('enable_normalization', String(document.getElementById('dda-detect-normalization')?.checked !== false));
  form.append('detection_sensitivity', String(Math.max(0, Math.min(1, Number(document.getElementById('dda-detect-sensitivity')?.value ?? 0.45)))));
  const minArea = Number(document.getElementById('dda-detect-min-area')?.value ?? 150);
  if (!Number.isNaN(minArea) && minArea >= 50) form.append('min_region_area', String(Math.round(minArea)));
  const notifyCb = document.getElementById('dda-detect-notify');
  const notifyEmail = document.getElementById('dda-detect-notify-email');
  if (notifyCb?.checked && notifyEmail?.value?.trim()) {
    form.append('notify_email', notifyEmail.value.trim());
  }

  try {
    const { result: data, jobId } = await runDetectionWithFallback(form, loading);
    if (jobId && typeof window.markDdaJobSeen === 'function') window.markDdaJobSeen(jobId);
    showDetectResult(data);
    if (typeof showDdaSuccess === 'function') {
      let msg = 'Detection complete.';
      if (data.notificationSent) msg += ' Report email sent.';
      showDdaSuccess(msg);
    }
    if (typeof loadReportsList === 'function') loadReportsList();
  } catch (err) {
    if (typeof showDdaError === 'function') showDdaError(err.message || 'Detection failed');
  } finally {
    btn.disabled = !(compareState.t1 && compareState.t2);
    loading?.classList.add('hidden');
  }
}

let compareInitialized = false;

function initCompareTab() {
  if (!compareInitialized) {
    setupCompareInteractions();
    compareInitialized = true;
  }
  updateRunButton();
  loadCompareLibraryGrid();
}

window.loadCompareLibraryGrid = loadCompareLibraryGrid;
window.initCompareTab = initCompareTab;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initCompareTab);
} else {
  initCompareTab();
}
