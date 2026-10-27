/* Replenishment order sheet: numbers save by themselves, rows can be ticked, and "proceed" waits until every ticked number is saved. */
(function () {
  'use strict';
  var msg = document.currentScript.dataset;
  var bulk = document.getElementById('bulk'), all = document.getElementById('repl-select-all'), bar = document.getElementById('repl-selection');
  var rowChecks = Array.prototype.slice.call(document.querySelectorAll('.rs-row-check'));
  var recChecks = Array.prototype.slice.call(document.querySelectorAll('.rc[form="bulk"]'));
  var forms = Array.prototype.slice.call(document.querySelectorAll('.repl-quantity'));
  var money = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  function dirty(f) { return Number(f.elements.qty.value) !== Number(f.dataset.saved); }
  function formOf(id) { return document.getElementById('repl-quantity-' + id); }
  function update() {
    var selected = recChecks.filter(function (c) { return c.checked; });
    var unsaved = selected.some(function (c) { var f = formOf(c.value); return f && (dirty(f) || f.dataset.saving === 'true'); });
    var value = 0;
    selected.forEach(function (c) { var f = formOf(c.value), q = f ? Number(f.elements.qty.value) : 0; value += (q || 0) * Number(c.dataset.unit || 0); });
    var rows = rowChecks.filter(function (c) { return c.checked; }).length;
    if (bar) {
      bar.hidden = selected.length === 0;
      document.getElementById('repl-selected-count').textContent = rows;
      document.getElementById('rs-selected-value').textContent = money.format(value);
      document.getElementById('repl-proceed').disabled = !selected.length || unsaved;
      document.getElementById('repl-save-hint').hidden = !unsaved;
    }
    if (all) {
      all.checked = rowChecks.length > 0 && rows === rowChecks.length;
      all.indeterminate = rows > 0 && rows < rowChecks.length;
      all.disabled = !rowChecks.length;
    }
  }
  rowChecks.forEach(function (rc) {
    rc.addEventListener('change', function () {
      var tr = rc.closest('tr');
      tr.querySelectorAll('.rc[form="bulk"]').forEach(function (c) { c.checked = rc.checked; });
      tr.classList.toggle('picked', rc.checked);
      update();
    });
  });
  if (all) all.addEventListener('change', function () {
    rowChecks.forEach(function (rc) { rc.checked = all.checked; rc.dispatchEvent(new Event('change')); });
  });
  async function save(form) {
    if (form.dataset.saving === 'true' || !dirty(form)) return;
    if (!form.reportValidity()) return;
    var status = form.querySelector('.repl-save-status'), input = form.elements.qty;
    form.dataset.saving = 'true'; input.classList.add('busy'); input.classList.remove('bad'); status.textContent = msg.saving; update();
    try {
      var response = await fetch(form.getAttribute('action'), { method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
      var result = await response.json();
      if (!response.ok) throw new Error(result.error || msg.failed);
      form.dataset.saved = result.quantity;
      status.textContent = '';
      input.classList.add('ok'); setTimeout(function () { input.classList.remove('ok'); }, 1200);
    } catch (e) {
      status.textContent = e.message || msg.failed; input.classList.add('bad'); input.title = status.textContent;
    } finally {
      form.dataset.saving = 'false'; input.classList.remove('busy'); update();
      if (dirty(form)) save(form);                       // typed again while saving
    }
  }
  forms.forEach(function (form) {
    var timer;
    form.addEventListener('input', function () { form.elements.qty.classList.remove('bad'); clearTimeout(timer); timer = setTimeout(function () { save(form); }, 700); update(); });
    form.addEventListener('change', function () { clearTimeout(timer); save(form); });
    form.addEventListener('submit', function (e) { e.preventDefault(); clearTimeout(timer); save(form); });
  });
  if (bulk) bulk.addEventListener('submit', function (e) { update(); if (document.getElementById('repl-proceed').disabled) e.preventDefault(); });
  window.addEventListener('pageshow', update);
  window.addEventListener('beforeunload', function (e) {
    if (forms.some(function (f) { return dirty(f) || f.dataset.saving === 'true'; })) { e.preventDefault(); e.returnValue = ''; }
  });
  update();
})();
