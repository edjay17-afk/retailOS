/* Sales by employee: metric bar switcher, client-side search & sorting, and slide-over employee detail drawer. */
(function () {
  'use strict';
  var page = document.querySelector('.emp-page');
  if (!page) return;

  var locSelect = document.getElementById('emp-loc');
  var filterForm = document.getElementById('emp-filter-form');
  if (locSelect && filterForm) {
    locSelect.addEventListener('change', function () { filterForm.submit(); });
  }

  // ---- Metric comparison switcher (Net sales, Transactions, Avg basket, Items / tx)
  var metric = 'net';
  var hbarList = document.getElementById('emp-hbar-list');
  function fmtMetric(key, val) {
    if (key === 'txs') return Math.round(val).toLocaleString();
    if (key === 'upt') return val.toFixed(1) + ' items';
    return '₱' + val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function renderMetricBars(key) {
    if (!hbarList) return;
    metric = key;
    var items = Array.prototype.slice.call(hbarList.querySelectorAll('.emp-hbar-item'));
    items.sort(function (a, b) {
      return parseFloat(b.getAttribute('data-' + key) || '0') - parseFloat(a.getAttribute('data-' + key) || '0');
    });
    var mx = Math.max.apply(null, items.map(function (el) { return parseFloat(el.getAttribute('data-' + key) || '0'); }).concat([1])) || 1;
    items.forEach(function (el, idx) {
      var val = parseFloat(el.getAttribute('data-' + key) || '0');
      var pct = Math.max(0, Math.min(100, val / mx * 100));
      el.classList.toggle('top', idx === 0);
      var rank = el.querySelector('.emp-hbar-rank');
      var fill = el.querySelector('.emp-hbar-fill');
      var readout = el.querySelector('.emp-hbar-val');
      if (rank) rank.textContent = String(idx + 1);
      if (fill) fill.style.width = pct.toFixed(1) + '%';
      if (readout) readout.textContent = fmtMetric(key, val);
      hbarList.appendChild(el);
    });
  }
  page.querySelectorAll('[data-emp-metric]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      page.querySelectorAll('[data-emp-metric]').forEach(function (b) { b.classList.toggle('on', b === btn); });
      renderMetricBars(btn.getAttribute('data-emp-metric') || 'net');
    });
  });

  // ---- Live search filter (keeps server totals accurate while narrowing visible rows)
  var searchInput = document.getElementById('emp-search');
  var shownLabel = document.getElementById('emp-shown');
  var table = document.getElementById('emp-table');
  var tbody = table && table.tBodies[0];
  function applySearch() {
    if (!tbody) return;
    var q = searchInput ? searchInput.value.trim().toLowerCase() : '';
    var shown = 0;
    Array.prototype.forEach.call(tbody.rows, function (tr) {
      var hay = tr.getAttribute('data-search') || '';
      var match = !q || hay.indexOf(q) !== -1;
      tr.hidden = !match;
      if (match) shown++;
    });
    if (shownLabel) {
      shownLabel.textContent = shown + ' ' + (shown === 1 ? 'cashier' : 'cashiers') + ' shown';
    }
    if (hbarList) {
      hbarList.querySelectorAll('.emp-hbar-item').forEach(function (el) {
        var hay = el.getAttribute('data-search') || '';
        el.hidden = !(!q || hay.indexOf(q) !== -1);
      });
    }
  }
  if (searchInput) searchInput.addEventListener('input', applySearch);

  // ---- Column sorting on employee table
  var sortState = { key: 'net', dir: -1 };
  var headers = table ? Array.prototype.slice.call(table.querySelectorAll('th.emp-sort')) : [];
  function sortTable(key) {
    if (!tbody) return;
    if (sortState.key === key) sortState.dir *= -1;
    else { sortState.key = key; sortState.dir = key === 'name' ? 1 : -1; }

    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      if (key === 'name') {
        var an = (a.getAttribute('data-name') || '').toLowerCase();
        var bn = (b.getAttribute('data-name') || '').toLowerCase();
        return an.localeCompare(bn) * sortState.dir;
      }
      var av = parseFloat(a.getAttribute('data-' + key) || '0');
      var bv = parseFloat(b.getAttribute('data-' + key) || '0');
      return (av - bv) * sortState.dir;
    });
    rows.forEach(function (r, idx) {
      var rankCell = r.querySelector('.emp-rank');
      if (rankCell) rankCell.textContent = String(idx + 1);
      tbody.appendChild(r);
    });
    headers.forEach(function (th) {
      var k = th.getAttribute('data-k');
      var active = k === sortState.key;
      th.classList.toggle('on', active);
      th.setAttribute('aria-sort', active ? (sortState.dir === 1 ? 'ascending' : 'descending') : 'none');
      var arrow = th.querySelector('.sort-dir');
      if (arrow) arrow.textContent = active ? (sortState.dir === 1 ? ' ↑' : ' ↓') : '';
    });
  }
  headers.forEach(function (th) {
    th.setAttribute('tabindex', '0');
    th.setAttribute('aria-sort', th.classList.contains('on') ? 'descending' : 'none');
    th.addEventListener('click', function () { sortTable(th.getAttribute('data-k')); });
    th.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sortTable(th.getAttribute('data-k')); }
    });
  });

  // ---- Slide-over employee detail drawer (capture phase overrides global [data-href] navigation on normal click)
  var drawer = document.getElementById('emp-drawer');
  var drawerBody = document.getElementById('emp-drawer-body');
  var drawerTitle = document.getElementById('emp-drawer-title');
  var drawerSub = document.getElementById('emp-drawer-sub');
  var drawerAv = document.getElementById('emp-drawer-av');
  var drawerScrim = document.getElementById('emp-drawer-scrim');
  var drawerCloseBtn = document.getElementById('emp-drawer-close');
  var reqSeq = 0;

  function closeDrawer() {
    if (!drawer) return;
    if (drawer.open) drawer.close();
    drawer.hidden = true;
    if (drawerScrim) drawerScrim.hidden = true;
    document.body.classList.remove('drawer-open');
    document.querySelectorAll('.emp-tw tbody tr.emp-on').forEach(function (r) { r.classList.remove('emp-on'); });
  }

  function openEmployeeDrawer(empKey, name, initials, storesText, fallbackUrl) {
    if (!drawer || !drawerBody) {
      if (fallbackUrl) window.location.href = fallbackUrl;
      return;
    }
    var panelBase = page.getAttribute('data-panel-url') || '/sales/employees/panel';
    var sep = panelBase.indexOf('?') === -1 ? '?' : '&';
    var url = panelBase + sep + 'emp=' + encodeURIComponent(empKey);
    var mine = ++reqSeq;

    if (drawerTitle) drawerTitle.textContent = name || 'Employee';
    if (drawerSub) drawerSub.textContent = storesText || '';
    if (drawerAv) drawerAv.textContent = initials || '—';
    drawerBody.innerHTML = '<p class="mute" style="padding:18px 0">Loading employee breakdown…</p>';

    drawer.hidden = false;
    if (drawerScrim) drawerScrim.hidden = true;
    document.body.classList.add('drawer-open');
    if (!drawer.open && typeof drawer.showModal === 'function') drawer.showModal();

    fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        if (mine !== reqSeq || drawer.hidden) return;
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var panel = doc.querySelector('.emp-panel-inner');
        if (!panel) throw new Error('missing panel');
        drawerBody.innerHTML = '';
        drawerBody.appendChild(panel);
        var closeFooter = drawerBody.querySelector('[data-emp-drawer-close]');
        if (closeFooter) closeFooter.addEventListener('click', closeDrawer);
      })
      .catch(function () {
        if (mine !== reqSeq || drawer.hidden) return;
        if (fallbackUrl) window.location.href = fallbackUrl;
        else drawerBody.innerHTML = '<p class="mute" style="padding:18px 0">Could not load employee breakdown.</p>';
      });
  }

  if (drawerCloseBtn) drawerCloseBtn.addEventListener('click', closeDrawer);
  if (drawer) {
    drawer.addEventListener('close', closeDrawer);
    drawer.addEventListener('click', function (e) {
      var b = drawer.getBoundingClientRect();
      if (e.target === drawer && (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom)) {
        closeDrawer();
      }
    });
  }

  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var trigger = e.target.closest && e.target.closest('[data-emp-open]');
    if (!trigger) return;
    if (e.target.closest('a,button,input,select,label') && e.target.closest('a,button,input,select,label') !== trigger) return;
    e.preventDefault();
    e.stopImmediatePropagation();

    document.querySelectorAll('.emp-tw tbody tr.emp-on').forEach(function (r) { r.classList.remove('emp-on'); });
    if (trigger.tagName === 'TR') trigger.classList.add('emp-on');

    openEmployeeDrawer(
      trigger.getAttribute('data-emp-open') || '__none__',
      trigger.getAttribute('data-emp-name') || '',
      trigger.getAttribute('data-emp-init') || '—',
      trigger.getAttribute('data-emp-stores') || '',
      trigger.getAttribute('data-href') || ''
    );
  }, true);
})();
