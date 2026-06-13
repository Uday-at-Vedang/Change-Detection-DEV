function renderYearTree(years) {
  const tree = document.getElementById('lib-tree');
  if (!tree) return;
  const filter = (document.getElementById('lib-tree-search')?.value || '').toLowerCase();

  const allBtn = `
    <button type="button" class="dda-tree-year ${window.ddaState.selectedYear === null ? 'active' : ''}" data-year="">
      All years
    </button>`;

  const yearBtns = (years || [])
    .filter((y) => !filter || String(y.year).includes(filter))
    .map((y) => `
      <button type="button" class="dda-tree-year ${window.ddaState.selectedYear === y.year ? 'active' : ''}" data-year="${y.year}">
        ${y.year} <span class="dim">(${y.imageCount})</span>
      </button>`).join('');

  tree.innerHTML = allBtn + yearBtns;

  tree.querySelectorAll('.dda-tree-year').forEach((btn) => {
    btn.addEventListener('click', () => {
      tree.querySelectorAll('.dda-tree-year').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const raw = btn.dataset.year;
      window.ddaState.setYear(raw ? parseInt(raw, 10) : null);
      window.ddaState.refreshImages();
    });
  });
}

document.getElementById('lib-tree-search')?.addEventListener('input', () => {
  if (window.ddaState?.years) renderYearTree(window.ddaState.years);
});
