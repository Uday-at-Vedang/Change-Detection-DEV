function renderHierarchy(data) {
  const tree = document.getElementById('lib-tree');
  if (!tree || !data?.zones) return;
  const filter = (document.getElementById('lib-tree-search')?.value || '').toLowerCase();

  tree.innerHTML = data.zones
    .filter((z) => !filter || z.name.toLowerCase().includes(filter) ||
      z.villages.some((v) => v.name.toLowerCase().includes(filter)))
    .map((zone) => `
      <div class="dda-tree-zone" data-zone-id="${zone.id}">
        <button type="button" class="dda-zone-toggle">${zone.name}</button>
        <div class="dda-tree-villages">
          ${zone.villages
            .filter((v) => !filter || v.name.toLowerCase().includes(filter) || zone.name.toLowerCase().includes(filter))
            .map((v) => `
            <button type="button" class="dda-tree-village" data-zone-id="${zone.id}" data-village-id="${v.id}">
              ${v.name}${v.imageCount ? ` (${v.imageCount})` : ''}
            </button>`).join('')}
        </div>
      </div>`).join('');

  tree.querySelectorAll('.dda-zone-toggle').forEach((btn) => {
    btn.addEventListener('click', () => {
      const zoneEl = btn.closest('.dda-tree-zone');
      zoneEl?.classList.toggle('open');
      const zoneId = parseInt(zoneEl?.dataset.zoneId, 10);
      window.ddaState.setSelection(zoneId, null);
      window.ddaState.refreshImages();
    });
  });

  tree.querySelectorAll('.dda-tree-village').forEach((btn) => {
    btn.addEventListener('click', () => {
      tree.querySelectorAll('.dda-tree-village').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      btn.closest('.dda-tree-zone')?.classList.add('open');
      window.ddaState.setSelection(
        parseInt(btn.dataset.zoneId, 10),
        parseInt(btn.dataset.villageId, 10),
      );
      window.ddaState.refreshImages();
    });
  });
}

document.getElementById('lib-tree-search')?.addEventListener('input', () => {
  if (window.ddaState?.hierarchy) renderHierarchy(window.ddaState.hierarchy);
});

function populateUploadSelects(data) {
  const zoneSel = document.getElementById('up-zone');
  const villageSel = document.getElementById('up-village');
  if (!zoneSel || !villageSel || !data?.zones) return;

  zoneSel.innerHTML = '<option value="">— Select —</option>';
  data.zones.forEach((z) => {
    const opt = document.createElement('option');
    opt.value = z.id;
    opt.textContent = z.name;
    zoneSel.appendChild(opt);
  });

  if (!zoneSel.dataset.bound) {
    zoneSel.dataset.bound = '1';
    zoneSel.addEventListener('change', () => {
      const hierarchy = window.ddaState?.hierarchy;
      const zid = parseInt(zoneSel.value, 10);
      villageSel.innerHTML = '<option value="">— Select —</option>';
      villageSel.disabled = !zid;
      if (!zid || !hierarchy) return;
      const zone = hierarchy.zones.find((z) => z.id === zid);
      (zone?.villages || []).forEach((v) => {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = v.name;
        villageSel.appendChild(opt);
      });
    });
  }
}

document.getElementById('form-upload')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideDdaError?.();
  const fileInput = document.getElementById('up-file');
  const file = fileInput?.files?.[0];
  if (!file) {
    showDdaError?.('Select a file to upload.');
    return;
  }

  const form = new FormData();
  form.append('file', file);
  form.append('zone_id', document.getElementById('up-zone').value);
  form.append('village_id', document.getElementById('up-village').value);
  form.append('area_name', document.getElementById('up-area').value || '');
  form.append('year', document.getElementById('up-year').value);
  form.append('capture_date', document.getElementById('up-date').value);
  form.append('source', document.getElementById('up-source').value);
  form.append('manual_bounds_json', document.getElementById('up-manual-bounds').value || '');

  const btn = document.getElementById('btn-upload');
  btn.disabled = true;
  btn.textContent = 'Uploading…';
  try {
    const data = await ddaApi('POST', '/api/dda/images/upload', { body: form });
    showDdaSuccess?.(data?.status === 'success' ? 'Image uploaded to library.' : 'Upload complete.');
    document.getElementById('form-upload').reset();
    document.getElementById('up-year').value = '2025';
    fileInput.value = '';
    window.ddaState.hierarchy = await ddaApi('GET', '/api/dda/hierarchy');
    renderHierarchy(window.ddaState.hierarchy);
    populateUploadSelects(window.ddaState.hierarchy);
    await window.ddaState.refreshImages();
  } catch (err) {
    showDdaError?.(err.message || 'Upload failed');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Upload to Library';
  }
});

// Default capture date = today
const dateInput = document.getElementById('up-date');
if (dateInput && !dateInput.value) {
  dateInput.value = new Date().toISOString().slice(0, 10);
}
