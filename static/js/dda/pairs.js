/** Same-area pairing: oldest = Before, newest = After, with manual override. */

const areaPairsState = { groups: [], unpaired: [], overrides: {}, autoApplied: false };

function _pairDateLabel(img) {
  if (!img) return '';
  if (img.captureDate) return String(img.captureDate).slice(0, 10);
  return '';
}

function _pairOptionLabel(img) {
  const date = _pairDateLabel(img);
  const name = img.filename || img.imageName || img.path || '';
  return date ? `${date} · ${name}` : name;
}

function _pairChoice(group) {
  const ov = areaPairsState.overrides[group.id];
  const images = group.images || [];
  const beforePath = ov?.beforePath || group.beforePath || group.suggestedBefore?.path;
  const afterPath = ov?.afterPath || group.afterPath || group.suggestedAfter?.path;
  const before = images.find((i) => i.path === beforePath) || group.suggestedBefore;
  const after = images.find((i) => i.path === afterPath) || group.suggestedAfter;
  return { before, after, overridden: !!ov };
}

function renderAreaPairRows(container, groups, { compact } = {}) {
  if (!container) return;
  if (!groups.length) {
    container.innerHTML = '<p class="dim">No same-area pairs yet. Upload two dates of the same place (or add capture dates / georef) and refresh.</p>';
    return;
  }
  container.innerHTML = groups.map((g) => {
    const { before, after, overridden } = _pairChoice(g);
    const opts = (g.images || []).map((img) => {
      const enc = encodeURIComponent(img.path);
      return `<option value="${enc}">${escapeHtml(_pairOptionLabel(img))}</option>`;
    }).join('');
    const match = g.match === 'bounds' ? 'overlapping map extent' : 'matching name / grid';
    const n = (g.images || []).length;
    return `
      <article class="dda-area-pair" data-group-id="${escapeHtml(g.id)}">
        <div class="dda-area-pair-head">
          <p class="dda-area-pair-title">${escapeHtml(g.label)}</p>
          <p class="dda-area-pair-meta dim">${n} image${n === 1 ? '' : 's'} · ${escapeHtml(match)}${overridden ? ' · <span class="dda-area-pair-overridden">overridden</span>' : ''}</p>
        </div>
        <label>Before (oldest)
          <select data-role="before">${opts}</select>
        </label>
        <label>After (newest)
          <select data-role="after">${opts}</select>
        </label>
        <div class="dda-area-pair-actions">
          <button type="button" class="btn btn-primary btn-sm" data-role="compare">Compare</button>
        </div>
      </article>`;
  }).join('');

  container.querySelectorAll('.dda-area-pair').forEach((row) => {
    const group = groups.find((g) => g.id === row.dataset.groupId);
    if (!group) return;
    const { before, after } = _pairChoice(group);
    const beforeSel = row.querySelector('select[data-role="before"]');
    const afterSel = row.querySelector('select[data-role="after"]');
    if (beforeSel && before) beforeSel.value = encodeURIComponent(before.path);
    if (afterSel && after) afterSel.value = encodeURIComponent(after.path);

    const saveOverride = () => {
      areaPairsState.overrides[group.id] = {
        beforePath: decodeURIComponent(beforeSel.value || ''),
        afterPath: decodeURIComponent(afterSel.value || ''),
      };
      renderAreaPairRows(document.getElementById('dda-area-pairs'), areaPairsState.groups);
      const cmp = document.getElementById('dda-compare-pairs');
      if (cmp && areaPairsState.groups.length) {
        renderAreaPairRows(cmp, areaPairsState.groups, { compact: true });
      }
    };
    beforeSel?.addEventListener('change', saveOverride);
    afterSel?.addEventListener('change', saveOverride);
    row.querySelector('[data-role="compare"]')?.addEventListener('click', () => {
      // Library page has no compare slots in its DOM — hand off to the
      // Detect page instead of trying to apply the pair locally.
      if (!document.getElementById('slot-t1')) {
        const mode = _pairChoice(group).overridden ? 'manual' : 'automatic';
        window.location.href = `/detect?pair=${encodeURIComponent(group.id)}&mode=${mode}`;
        return;
      }
      const choice = _pairChoice(group);
      areaPairsState.selectedGroupId = group.id;
      if (choice.overridden) {
        if (typeof setDetectMode === 'function') setDetectMode('manual', { applyPair: false });
        if (typeof applyAreaPairToCompare === 'function') {
          applyAreaPairToCompare(choice.before, choice.after, { silent: false });
        }
      } else {
        if (typeof setDetectMode === 'function') setDetectMode('automatic', { applyPair: false });
        const areaSel = document.getElementById('dda-auto-area');
        if (areaSel) areaSel.value = group.id;
        applySelectedAutoPair();
      }
    });
  });
}

function applySelectedAutoPair() {
  const sel = document.getElementById('dda-auto-area');
  const status = document.getElementById('dda-auto-status');
  const groupId = sel?.value || areaPairsState.selectedGroupId;
  const group = (areaPairsState.groups || []).find((g) => g.id === groupId);
  areaPairsState.selectedGroupId = group?.id || '';
  if (typeof getDetectMode === 'function' && getDetectMode() !== 'automatic') return;
  if (!group) {
    if (status) status.textContent = 'No same-area pair found. Upload two dates of the same place, or switch to Manual.';
    return;
  }
  const before = group.suggestedBefore;
  const after = group.suggestedAfter;
  if (typeof applyAreaPairToCompare === 'function') {
    applyAreaPairToCompare(before, after, { silent: true });
  }
  const bDate = group.beforeDate || _pairDateLabel(before) || 'oldest';
  const aDate = group.afterDate || _pairDateLabel(after) || 'newest';
  if (status) {
    status.textContent = `Selected automatically: Before = ${before?.filename || '—'} (${bDate}) → After = ${after?.filename || '—'} (${aDate}). Click Run to start change detection.`;
  }
}

function populateAutoAreaSelect() {
  const sel = document.getElementById('dda-auto-area');
  if (!sel) return;
  const groups = areaPairsState.groups || [];
  const prev = sel.value || areaPairsState.selectedGroupId || '';
  if (!groups.length) {
    sel.innerHTML = '<option value="">No same-area pairs found</option>';
    areaPairsState.selectedGroupId = '';
    applySelectedAutoPair();
    return;
  }
  sel.innerHTML = groups.map((g) => {
    const n = (g.images || []).length;
    return `<option value="${escapeHtml(g.id)}">${escapeHtml(g.label)} · ${n} image${n === 1 ? '' : 's'}</option>`;
  }).join('');
  if (prev && groups.some((g) => g.id === prev)) sel.value = prev;
  else sel.value = groups[0].id;
  areaPairsState.selectedGroupId = sel.value;
  applySelectedAutoPair();
}

// Consumes ?pair=<groupId>&mode=automatic|manual once, set by the Library
// page's "Compare" button when it navigates to /detect (see renderAreaPairRows).
function applyPairFromUrl() {
  if (areaPairsState._urlPairApplied) return;
  const params = new URLSearchParams(window.location.search);
  const wantPair = params.get('pair');
  if (!wantPair) return;
  areaPairsState._urlPairApplied = true;
  const group = areaPairsState.groups.find((g) => g.id === wantPair);
  if (!group) return;
  areaPairsState.selectedGroupId = group.id;
  if (params.get('mode') === 'manual') {
    if (typeof setDetectMode === 'function') setDetectMode('manual', { applyPair: false });
    const choice = _pairChoice(group);
    if (typeof applyAreaPairToCompare === 'function') {
      applyAreaPairToCompare(choice.before, choice.after, { silent: false });
    }
  } else {
    if (typeof setDetectMode === 'function') setDetectMode('automatic', { applyPair: false });
    const areaSel = document.getElementById('dda-auto-area');
    if (areaSel) areaSel.value = group.id;
    applySelectedAutoPair();
  }
}

async function loadAreaPairs({ nodeId, autoFillCompare } = {}) {
  const libBox = document.getElementById('dda-area-pairs');
  const hint = document.getElementById('dda-area-pairs-hint');
  const cmpBox = document.getElementById('dda-compare-pairs');
  const params = new URLSearchParams();
  if (nodeId) params.set('node_id', String(nodeId));
  try {
    const data = await ddaApi('GET', '/api/dda/local/area-groups' + (params.toString() ? `?${params}` : ''));
    areaPairsState.groups = data.groups || [];
    areaPairsState.unpaired = data.unpaired || [];
    window.ddaState = window.ddaState || {};
    window.ddaState.areaGroups = areaPairsState.groups;
    const n = areaPairsState.groups.length;
    const unpaired = areaPairsState.unpaired.length;
    if (hint) {
      hint.textContent = n
        ? `${n} pair${n === 1 ? '' : 's'} · ${unpaired} unpaired`
        : (unpaired ? `${unpaired} image(s) not yet paired` : '');
    }
    renderAreaPairRows(libBox, areaPairsState.groups);
    if (cmpBox) {
      if (!areaPairsState.groups.length) cmpBox.innerHTML = '';
      else renderAreaPairRows(cmpBox, areaPairsState.groups, { compact: true });
    }
    populateAutoAreaSelect();
    applyPairFromUrl();
    if (autoFillCompare && !areaPairsState.autoApplied && areaPairsState.groups.length) {
      if (typeof getDetectMode !== 'function' || getDetectMode() !== 'automatic') {
        const first = areaPairsState.groups[0];
        const choice = _pairChoice(first);
        if (typeof applyAreaPairToCompare === 'function') {
          applyAreaPairToCompare(choice.before, choice.after, { silent: true, onlyIfEmpty: true });
        }
      }
      areaPairsState.autoApplied = true;
    }
  } catch (err) {
    if (libBox) libBox.innerHTML = `<p class="dim">Could not load same-area pairs: ${escapeHtml(err.message || 'error')}</p>`;
  }
}

window.loadAreaPairs = loadAreaPairs;
window.areaPairsState = areaPairsState;
window.applySelectedAutoPair = applySelectedAutoPair;
