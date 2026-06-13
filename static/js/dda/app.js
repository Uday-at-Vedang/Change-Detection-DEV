const API = '';

async function ddaApi(method, path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(API + path, { method, headers, credentials: 'include', ...options });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) {}
  if (!res.ok) throw new Error(data?.detail || res.statusText || 'Request failed');
  return data;
}

function showDdaError(msg) {
  const el = document.getElementById('dda-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
}
function hideDdaError() {
  document.getElementById('dda-error')?.classList.add('hidden');
}
function showDdaSuccess(msg) {
  const el = document.getElementById('dda-success');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 4000);
}

let ddaConfig = null;
let ddaHierarchy = null;
let selectedVillageId = null;
let selectedZoneId = null;

window.ddaState = {
  get hierarchy() { return ddaHierarchy; },
  get config() { return ddaConfig; },
  get selectedVillageId() { return selectedVillageId; },
  get selectedZoneId() { return selectedZoneId; },
  setSelection(zoneId, villageId) {
    selectedZoneId = zoneId;
    selectedVillageId = villageId;
  },
  refreshImages: () => loadLibraryImages(),
};

document.querySelectorAll('.dda-tab').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.dda-tab').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.dda-panel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    document.getElementById('tab-' + tab)?.classList.add('active');
  });
});

async function initDda() {
  hideDdaError();
  try {
    ddaConfig = await ddaApi('GET', '/api/dda/config');
    const hint = document.getElementById('lib-config-hint');
    if (hint) {
      const gb = ddaConfig.maxUploadGb;
      const label = gb >= 1 ? `${gb} GB GeoTIFF` : `${ddaConfig.maxGeotiffMb || ddaConfig.maxUploadMb} MB`;
      hint.textContent = `Max ${label} · GeoTIFF engine: ${ddaConfig.geotiffEnabled ? 'yes' : 'limited'}`;
    }
    ddaHierarchy = await ddaApi('GET', '/api/dda/hierarchy');
    if (typeof renderHierarchy === 'function') renderHierarchy(ddaHierarchy);
    if (typeof populateUploadSelects === 'function') populateUploadSelects(ddaHierarchy);
    await loadLibraryImages();
  } catch (err) {
    showDdaError(err.message || 'Failed to load DDA configuration');
  }
}

async function loadLibraryImages() {
  const grid = document.getElementById('lib-grid');
  if (!grid) return;
  const q = document.getElementById('lib-filter')?.value?.trim() || '';
  const params = new URLSearchParams();
  if (selectedVillageId) params.set('village_id', String(selectedVillageId));
  else if (selectedZoneId) params.set('zone_id', String(selectedZoneId));
  if (q) params.set('q', q);
  try {
    const items = await ddaApi('GET', '/api/dda/images?' + params.toString());
    if (!items.length) {
      grid.innerHTML = '<p class="dim">No images yet. Upload a GeoTIFF or image above.</p>';
      return;
    }
    grid.innerHTML = items.map((img) => `
      <div class="dda-card-img" draggable="true" data-image-id="${img.id}" title="${img.originalFilename}">
        ${img.thumbUrl ? `<img src="${img.thumbUrl}" alt="" loading="lazy" />` : '<div class="meta">No preview</div>'}
        <div class="meta">
          <strong>${img.year || '—'}</strong><br/>
          ${img.captureDate || ''}<br/>
          ${img.villageName || img.zoneName || ''}
        </div>
      </div>`).join('');
    grid.querySelectorAll('.dda-card-img').forEach((card) => {
      card.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', card.dataset.imageId);
      });
    });
  } catch (err) {
    grid.innerHTML = `<p class="dim">Could not load images: ${err.message}</p>`;
  }
}

document.getElementById('lib-filter')?.addEventListener('input', () => {
  clearTimeout(window._libFilterTimer);
  window._libFilterTimer = setTimeout(loadLibraryImages, 300);
});

initDda();
