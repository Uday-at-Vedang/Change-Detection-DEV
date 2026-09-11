/** Same-area pairing: oldest = Before, newest = After, with manual override. */

const areaPairsState = { groups: [], unpaired: [], overrides: {}, autoApplied: false };

function _pairDateLabel(img) {
  if (!img) return '';
  if (img.captureDate) return String(img.captureDate).slice(0, 10);
  return '';
}

function _pairOptionLabel(img) {
  const name = img.filename || img.imageName || img.path || '';
  const parts = [_pairDateLabel(img), name, img.villageName || img.zoneName || ''];
  return parts.filter(Boolean).join(' · ');
}

function _assignedPaths(exceptGroupId) {
  const used = new Set();
  for (const group of areaPairsState.groups || []) {
    if (group.id === exceptGroupId) continue;
    const ov = areaPairsState.overrides[group.id];
    const before = ov?.beforePath || group.beforePath;
    const after = ov?.afterPath || group.afterPath;
    if (before) used.add(before);
    if (after) used.add(after);
  }
  return used;
}

// Automatic pairing is always two images. The dropdown may offer unused
// images from the same place, but never an image already assigned elsewhere.
function _pairOptions(group, { excludePath } = {}) {
  const used = _assignedPaths(group.id);
  const pool = [];
  const seen = new Set();
  const add = (img) => {
    if (!img?.path || seen.has(img.path)) return;
    if (excludePath && img.path === excludePath) return;
    seen.add(img.path);
    pool.push(img);
  };
  (group.images || []).forEach(add);
  (group.candidates || []).forEach((img) => {
    if (used.has(img.path)) return;
    add(img);
  });
  return pool;
}

function _pairChoice(group) {
  const ov = areaPairsState.overrides[group.id];
  const beforePath = ov?.beforePath || group.beforePath || group.suggestedBefore?.path;
  const afterPath = ov?.afterPath || group.afterPath || group.suggestedAfter?.path;
  const images = _pairOptions(group);
  const before = images.find((i) => i.path === beforePath) || group.suggestedBefore;
  const after = images.find((i) => i.path === afterPath) || group.suggestedAfter;
  if (before?.path && after?.path && before.path === after.path) {
    return { before: group.suggestedBefore, after: group.suggestedAfter, overridden: false };
  }
  return { before, after, overridden: !!ov };
}

function _pairReasonText(group) {
  if (group.reasons?.length) return group.reasons.join(' · ');
  return group.match === 'bounds' ? 'overlapping map extent' : 'matching name / grid';
}

function renderAreaPairRows(container, groups, { compact } = {}) {
  if (!container) return;
  if (!groups.length) {
    container.innerHTML = '<p class="dim">No same-area pairs yet. Upload two dates of the same place (or add capture dates / georef) and refresh.</p>';
    return;
  }
  container.innerHTML = groups.map((g) => {
    const { before, after, overridden } = _pairChoice(g);
    const optionHtml = (excludePath) => _pairOptions(g, { excludePath }).map((img) => {
      const enc = encodeURIComponent(img.path);
      return `<option value="${enc}">${escapeHtml(_pairOptionLabel(img))}</option>`;
    }).join('');
    const extra = Math.max(0, _pairOptions(g).length - 2);
    return `
      <article class="dda-area-pair" data-group-id="${escapeHtml(g.id)}">
        <div class="dda-area-pair-head">
          <p class="dda-area-pair-title">${escapeHtml(g.label)}</p>
          <p class="dda-area-pair-meta dim">${escapeHtml(_pairReasonText(g))}${extra ? ` · ${extra} other image${extra === 1 ? '' : 's'} here` : ''}${overridden ? ' · <span class="dda-area-pair-overridden">overridden</span>' : ''}</p>
        </div>
        <label>Before (oldest)
          <select data-role="before">${optionHtml(after?.path)}</select>
        </label>
        <label>After (newest)
          <select data-role="after">${optionHtml(before?.path)}</select>
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
      const beforePath = decodeURIComponent(beforeSel.value || '');
      const afterPath = decodeURIComponent(afterSel.value || '');
      if (!beforePath || !afterPath || beforePath === afterPath) {
        if (typeof showDdaError === 'function') {
          showDdaError('A pair must be two different images. The same file cannot be Before and After.');
        }
        renderAreaPairRows(document.getElementById('dda-area-pairs'), areaPairsState.groups);
        const cmp = document.getElementById('dda-compare-pairs');
        if (cmp && areaPairsState.groups.length) {
          renderAreaPairRows(cmp, areaPairsState.groups, { compact: true });
        }
        return;
      }
      areaPairsState.overrides[group.id] = { beforePath, afterPath };
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
    if (typeof updateAutoScheduleLine === 'function') updateAutoScheduleLine();
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
    status.textContent = `Selected automatically: Before = ${before?.filename || '—'} (${bDate}) → After = ${after?.filename || '—'} (${aDate}). Use Run for a one-off detection. The schedule only runs after you click Start.`;
  }
  if (typeof updateAutoScheduleLine === 'function') updateAutoScheduleLine();
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
    const span = [g.beforeDate, g.afterDate].filter(Boolean).join(' → ');
    return `<option value="${escapeHtml(g.id)}">${escapeHtml(g.label)}${span ? ` · ${escapeHtml(span)}` : ''}</option>`;
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
    if (typeof populateSelects === 'function' && typeof selectSourceItems === 'function') {
      populateSelects(selectSourceItems((window.ddaState && window.ddaState.libraryItems) || []));
    }
    if (typeof loadAutoDetectStatus === 'function') loadAutoDetectStatus();
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
window.loadAutoDetectStatus = loadAutoDetectStatus;
window.updateAutoScheduleLine = updateAutoScheduleLine;
window.initAutoScheduleControls = initAutoScheduleControls;

function _scheduleWhen(iso) {
  if (!iso) return 'not yet';
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return iso;
  }
}

function fillAutoScheduleForm(data) {
  const interval = document.getElementById('dda-auto-interval');
  const runAt = document.getElementById('dda-auto-run-at');
  if (interval && document.activeElement !== interval) {
    interval.value = String(data.intervalDays || 10);
  }
  if (runAt && document.activeElement !== runAt) {
    runAt.value = data.runAt || '02:00';
  }
  const start = document.getElementById('btn-auto-schedule-start');
  const stop = document.getElementById('btn-auto-schedule-stop');
  const canStart = typeof hasPermission === 'function' && window.ddaPermissions
    ? hasPermission(window.ddaPermissions, 'detect', 'create')
    : false;
  if (start) {
    start.textContent = data.running ? 'Update schedule' : 'Start schedule';
    if (!canStart) start.disabled = true;
  }
  if (stop) stop.disabled = !data.running || !canStart;
}

function updateAutoScheduleLine() {
  const box = document.getElementById('dda-auto-schedule');
  if (!box) return;
  const data = (window.ddaState && window.ddaState.autoDetect) || null;
  if (!data) {
    box.textContent = '';
    return;
  }
  if (!data.enabled) {
    box.textContent = 'Background schedule is turned off on the server.';
    fillAutoScheduleForm(data);
    return;
  }
  fillAutoScheduleForm(data);
  const days = data.intervalDays || 10;
  if (!data.running) {
    box.textContent = `Schedule is stopped. It does not start when the app launches. Set interval and time, then click Start. First queue will be at that time (IST), then every ${days} day${days === 1 ? '' : 's'}.`;
    return;
  }
  const groupId = document.getElementById('dda-auto-area')?.value;
  const group = (areaPairsState.groups || []).find((g) => g.id === groupId);
  const before = group?.suggestedBefore?.path || group?.beforePath;
  const after = group?.suggestedAfter?.path || group?.afterPath;
  const pair = (data.pairs || []).find((p) => p.beforePath === before && p.afterPath === after);
  const next = _scheduleWhen(data.nextRunAt);
  const last = pair?.lastCompletedAt ? _scheduleWhen(pair.lastCompletedAt) : 'not yet';
  let line = `Schedule running · every ${days} days at ${data.runAt || '02:00'} IST · next queue ${next} · last report ${last}.`;
  if (pair?.lastJobStatus === 'queued' || pair?.lastJobStatus === 'running') {
    line += ` Job #${pair.lastJobId} ${pair.lastJobStatus}.`;
  }
  if (pair?.lastError) line += ` Last error: ${pair.lastError}`;
  box.textContent = line;
}

async function loadAutoDetectStatus() {
  try {
    const data = await ddaApi('GET', '/api/dda/auto-detect');
    window.ddaState = window.ddaState || {};
    window.ddaState.autoDetect = data;
    updateAutoScheduleLine();
    if (typeof setDetectMode === 'function' && typeof getDetectMode === 'function' && getDetectMode() === 'automatic') {
      setDetectMode('automatic', { applyPair: false });
    }
  } catch (_) {
    const box = document.getElementById('dda-auto-schedule');
    if (box) box.textContent = '';
  }
}

function initAutoScheduleControls() {
  const start = document.getElementById('btn-auto-schedule-start');
  const stop = document.getElementById('btn-auto-schedule-stop');
  if (!start || start.dataset.bound) return;
  start.dataset.bound = '1';
  start.addEventListener('click', async () => {
    const interval = Number(document.getElementById('dda-auto-interval')?.value || 10);
    const runAt = document.getElementById('dda-auto-run-at')?.value || '02:00';
    start.disabled = true;
    try {
      const data = await ddaApi('POST', '/api/dda/auto-detect/start', {
        body: JSON.stringify({ intervalDays: interval, runAt }),
      });
      window.ddaState = window.ddaState || {};
      window.ddaState.autoDetect = data;
      updateAutoScheduleLine();
      if (typeof showDdaSuccess === 'function') {
        showDdaSuccess(`Schedule started. First automatic queue at ${_scheduleWhen(data.nextRunAt)} (not now).`);
      }
    } catch (err) {
      if (typeof showDdaError === 'function') showDdaError(err.message || 'Could not start schedule');
    } finally {
      start.disabled = false;
    }
  });
  stop?.addEventListener('click', async () => {
    stop.disabled = true;
    try {
      const data = await ddaApi('POST', '/api/dda/auto-detect/stop');
      window.ddaState = window.ddaState || {};
      window.ddaState.autoDetect = data;
      updateAutoScheduleLine();
      if (typeof showDdaSuccess === 'function') showDdaSuccess('Automatic schedule stopped.');
    } catch (err) {
      if (typeof showDdaError === 'function') showDdaError(err.message || 'Could not stop schedule');
      stop.disabled = false;
    }
  });
}
