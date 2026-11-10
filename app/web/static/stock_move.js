/* Stock compare > Move from warehouse: tick products, edit the quantity, see the total, create the sheet. */
(function () {
  'use strict';
  var form = document.getElementById('mv-form');
  if (!form) return;
  var T = document.currentScript.dataset, all = document.getElementById('mv-all'), go = document.getElementById('mv-go'), sel = document.getElementById('mv-sel');
  var rows = Array.prototype.slice.call(form.querySelectorAll('input.mv-row'));
  function qty(tr) { var q = tr.querySelector('.mv-q'); return q ? Math.max(0, parseInt(q.value, 10) || 0) : 0; }
  function update() {
    var n = 0, pcs = 0, bad = false;
    rows.forEach(function (c) {
      var tr = c.closest('tr'), q = tr.querySelector('.mv-q');
      tr.classList.toggle('mv-picked', c.checked);
      if (c.checked) { n++; pcs += qty(tr); if (q && qty(tr) < 1) bad = true; }
      if (q) q.classList.toggle('bad', c.checked && qty(tr) < 1);
    });
    if (sel) sel.textContent = n ? (n + ' ' + T.products + ' \u00b7 ' + pcs + ' ' + T.pcs) : '0';
    if (go) go.disabled = !n || bad;
    if (all) { all.checked = rows.length > 0 && n === rows.length; all.indeterminate = n > 0 && n < rows.length; }
  }
  rows.forEach(function (c) { c.addEventListener('change', update); });
  form.querySelectorAll('.mv-q').forEach(function (q) {
    q.addEventListener('input', function () { var c = q.closest('tr').querySelector('.mv-row'); if (c && !c.checked && q.value) c.checked = true; update(); });
  });
  if (all) all.addEventListener('change', function () { var on = all.checked; rows.forEach(function (c) { c.checked = on; }); update(); });
  update();
})();
