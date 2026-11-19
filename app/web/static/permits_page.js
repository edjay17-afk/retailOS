(function () {
  'use strict';
  var search = document.getElementById('permit-search');
  var locSel = document.getElementById('permit-loc-filter');
  var chips = Array.prototype.slice.call(document.querySelectorAll('#permit-status-chips .permits-chip'));
  var rows = Array.prototype.slice.call(document.querySelectorAll('#permit-table tbody tr[data-status]'));
  var emptyRow = document.getElementById('permit-filter-empty');
  if (!rows.length) return;

  var activeStatus = 'all';

  function apply() {
    var q = search ? search.value.trim().toLowerCase() : '';
    var loc = locSel ? locSel.value : 'all';
    var shown = 0;
    rows.forEach(function (tr) {
      var okStatus = activeStatus === 'all' || tr.dataset.status === activeStatus;
      var okLoc = loc === 'all' || tr.dataset.loc === loc;
      var okQuery = !q || (tr.dataset.search || '').indexOf(q) !== -1;
      var show = okStatus && okLoc && okQuery;
      tr.hidden = !show;
      if (show) shown++;
    });
    if (emptyRow) emptyRow.hidden = shown > 0;
  }

  chips.forEach(function (btn) {
    btn.addEventListener('click', function () {
      activeStatus = btn.dataset.filter || 'all';
      chips.forEach(function (c) {
        var on = c === btn;
        c.classList.toggle('on', on);
        c.setAttribute('aria-pressed', String(on));
      });
      apply();
    });
  });

  if (search) search.addEventListener('input', apply);
  if (locSel) locSel.addEventListener('change', apply);
})();
