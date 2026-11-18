/* Replenishment order sheet: numbers save by themselves, rows can be ticked, and "proceed" waits until every ticked number is saved. */
(function () {
  'use strict';
  var msg = document.currentScript.dataset;
  var bulk = document.getElementById('bulk'), all = document.getElementById('repl-select-all'), bar = document.getElementById('repl-selection');
  var panel = document.getElementById('rs-panel');
  var rowChecks = Array.prototype.slice.call(document.querySelectorAll('.rs-row-check'));
  var recChecks = Array.prototype.slice.call(document.querySelectorAll('.rc[form="bulk"]'));
  var forms = Array.prototype.slice.call(document.querySelectorAll('.repl-quantity'));
  var phoneRows = Array.prototype.slice.call(document.querySelectorAll('.rs-row'));
  var phone = window.matchMedia('(max-width:700px)');
  function setPhoneRow(row) {
    phoneRows.forEach(function (r) {
      var open = r === row;
      r.classList.toggle('rs-expanded', open);
      var button = r.querySelector('.rs-phone-open');
      if (button) { button.setAttribute('aria-expanded', String(open)); button.textContent = open ? button.dataset.closeLabel : button.dataset.openLabel; }
    });
  }
  if (phoneRows.length) {
    document.querySelector('.rs-wrap').classList.add('rs-phone-enhanced');
    phoneRows.forEach(function (row) {
      var button = row.querySelector('.rs-phone-open');
      if (button) button.addEventListener('click', function () { setPhoneRow(row.classList.contains('rs-expanded') ? null : row); });
    });
    setPhoneRow(phoneRows[0]);
  }
  // phone: the store and "what to do" filters stay folded behind one Filter button
  var filterToggle = document.querySelector('.rs-filter-toggle');
  if (filterToggle) filterToggle.addEventListener('click', function () {
    var open = filterToggle.form.classList.toggle('rs-filters-open');
    filterToggle.setAttribute('aria-expanded', String(open));
  });

  var money = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  function dirty(f) { return Number(f.elements.qty.value) !== Number(f.dataset.saved); }
  function formOf(id) { return document.getElementById('repl-quantity-' + id); }
  // The ticked products are kept for this browser tab, so they stay ticked when you go to the next page or change a filter.
  var KEY = 'ros-repl-sel', store = {};
  try { var saved0 = JSON.parse(sessionStorage.getItem(KEY) || '{}'); if (saved0.t && Date.now() - saved0.t < 12 * 3600 * 1000 && saved0.rows) store = saved0.rows; } catch (e) { store = {}; }
  function persist() { try { sessionStorage.setItem(KEY, JSON.stringify({ t: Date.now(), rows: store })); } catch (e) { /* private window: selection just stays on this page */ } }
  function pidOf(rc) { var tr = rc.closest('tr'); return tr ? tr.dataset.pid : ''; }
  function rowValue(tr) {
    var v = 0;
    tr.querySelectorAll('.rc[form="bulk"]').forEach(function (c) { var f = formOf(c.value), q = f ? Number(f.elements.qty.value) : 0; v += (q || 0) * Number(c.dataset.unit || 0); });
    return v;
  }
  // What the warehouse sends to each store and what is left to order. The warehouse stock is shared between the stores in order, as the server does it when you proceed.
  function planRow(tr) {
    var cells = [];
    tr.querySelectorAll('.s-chip[data-loc]').forEach(function (chip) {
      var c = chip.querySelector('.rc[form="bulk"]');
      if (!c) return;
      var f = formOf(c.value);
      cells.push({ loc: Number(chip.dataset.loc), name: chip.dataset.label || '', qty: f ? Number(f.elements.qty.value) || 0 : Number(c.dataset.qty || 0), unit: Number(c.dataset.unit || 0) });
    });
    cells.sort(function (a, b) { return a.loc - b.loc; });
    var hasWh = !panel || panel.dataset.hasWh === '1';
    var whRaw = tr.dataset.wh != null && tr.dataset.wh !== '' ? Math.max(0, Number(tr.dataset.wh)) : null;
    var left = whRaw === null ? (hasWh ? 0 : Infinity) : Math.floor(whRaw);
    var need = 0, send = 0, sends = [], unit = 0;
    cells.forEach(function (x) {
      var s = Math.min(x.qty, left); left -= s; need += x.qty; send += s; unit = unit || x.unit;
      if (s > 0) sends.push({ name: x.name, qty: s });
    });
    var order = Math.max(0, need - send);
    return { send: send, order: order, sends: sends, po: order > 0 ? { supplier: tr.dataset.supplier || '', qty: order, value: order * unit } : null };
  }
  function docLine(parent, title, sub, amount, cls) {
    var row = document.createElement('div'); row.className = 'rs-doc ' + cls;
    var ic = document.createElement('span'); ic.className = 'rs-doc-ic'; ic.innerHTML = '<svg class="i" aria-hidden="true"><use href="#i-' + (cls === 'send' ? 'sync' : 'file') + '"/></svg>';
    var copy = document.createElement('div'), b = document.createElement('b'), small = document.createElement('small');
    b.textContent = title; small.textContent = sub; copy.appendChild(b); copy.appendChild(small);
    row.appendChild(ic); row.appendChild(copy);
    if (amount) { var a = document.createElement('span'); a.className = 'rs-doc-amt num'; a.textContent = amount; row.appendChild(a); }
    parent.appendChild(row);
  }
  function renderPanel(keys, value, unsaved) {
    if (!panel) return;
    var d = panel.dataset, transfers = {}, orders = {}, nTransfers = 0, suppliers = 0;
    keys.forEach(function (k) {
      (store[k].sends || []).forEach(function (x) { var t = transfers[x.name] || (transfers[x.name] = { lines: 0, units: 0 }); t.lines++; t.units += x.qty; });
      var po = store[k].po;
      if (po) { var o = orders[po.supplier] || (orders[po.supplier] = { lines: 0, units: 0, value: 0 }); o.lines++; o.units += po.qty; o.value += po.value; }
    });
    var sendList = document.getElementById('rsp-send-list'), orderList = document.getElementById('rsp-order-list');
    sendList.textContent = ''; orderList.textContent = '';
    Object.keys(transfers).sort().forEach(function (name) {
      var t = transfers[name]; nTransfers++;
      docLine(sendList, d.to + ' ' + name, t.lines + ' ' + (t.lines === 1 ? d.line : d.lines) + ' · ' + Math.round(t.units) + ' ' + d.units, '', 'send');
    });
    Object.keys(orders).sort().forEach(function (name) {
      var o = orders[name]; suppliers++;
      docLine(orderList, name || '—', o.lines + ' ' + (o.lines === 1 ? d.line : d.lines) + ' · ' + Math.round(o.units) + ' ' + d.units, '₱' + money.format(o.value), 'order');
    });
    document.getElementById('rsp-send').hidden = nTransfers === 0;
    document.getElementById('rsp-order').hidden = suppliers === 0;
    document.getElementById('rsp-empty').hidden = keys.length > 0;
    document.getElementById('rsp-value').textContent = '₱' + money.format(value);
    document.getElementById('rsp-count-line').textContent = keys.length ? keys.length + ' ' + (keys.length === 1 ? d.selectedOne : d.selected) : d.none;
    var multi = suppliers > 1, warn = document.getElementById('rsp-warn');
    warn.hidden = !multi; warn.textContent = multi ? d.multi : '';
    document.getElementById('rsp-hint').hidden = !unsaved;
    var go = document.getElementById('rsp-proceed'), docs = nTransfers + (suppliers ? 1 : 0);
    go.disabled = !keys.length || unsaved || multi;
    go.textContent = d.canDocs && docs ? (docs === 1 ? d.one : d.many.replace('{n}', docs)) : d.default;
  }
  function update() {
    var selected = recChecks.filter(function (c) { return c.checked; });
    var unsaved = selected.some(function (c) { var f = formOf(c.value); return f && (dirty(f) || f.dataset.saving === 'true'); });
    var rows = rowChecks.filter(function (c) { return c.checked; }).length;
    rowChecks.forEach(function (rc) {          // this page's rows into the kept selection
      var pid = pidOf(rc), tr = rc.closest('tr');
      if (!pid) return;
      if (rc.checked) {
        var plan = planRow(tr);
        store[pid] = { ids: Array.prototype.map.call(tr.querySelectorAll('.rc[form="bulk"]'), function (c) { return c.value; }), value: rowValue(tr), todo: tr.dataset.todo || 'send', send: plan.send, order: plan.order, sends: plan.sends, po: plan.po };
      }
      else delete store[pid];
    });
    persist();
    var keys = Object.keys(store), value = 0, sendSum = 0, orderSum = 0, kinds = {};
    keys.forEach(function (k) { value += store[k].value || 0; });
    renderPanel(keys, value, unsaved);
    value = 0;
    keys.forEach(function (k) { value += store[k].value || 0; sendSum += store[k].send || 0; orderSum += store[k].order || 0; kinds[store[k].todo || 'send'] = true; });
    if (bar) {
      bar.hidden = keys.length === 0;
      document.getElementById('repl-selected-count').textContent = keys.length;
      document.getElementById('rs-selected-value').textContent = money.format(value);
      var splitEl = document.getElementById('rs-selected-split');
      if (splitEl) {
        var parts = [];
        if (sendSum > 0) parts.push('Transfer ' + Math.round(sendSum));
        if (orderSum > 0) parts.push('Order ' + Math.round(orderSum));
        splitEl.textContent = parts.length ? '· ' + parts.join(' · ') : '';
      }
      var btn = document.getElementById('repl-proceed');
      btn.disabled = !keys.length || unsaved;
      document.getElementById('repl-save-hint').hidden = !unsaved;
      var wantsSheet = kinds.send || kinds.both, wantsPo = kinds.order || kinds.both;
      if (btn.dataset.sheet) btn.textContent = wantsSheet && wantsPo ? btn.dataset.both : wantsPo ? btn.dataset.po : wantsSheet ? btn.dataset.sheet : btn.dataset.default;
      alignDock();
    }
    if (all) {
      all.checked = rowChecks.length > 0 && rows === rowChecks.length;
      all.indeterminate = rows > 0 && rows < rowChecks.length;
      all.disabled = !rowChecks.length;
    }
  }
  rowChecks.forEach(function (rc) {            // ticked on an earlier page or before a filter: tick it again here
    var pid = pidOf(rc);
    if (pid && store[pid]) { rc.checked = true; var tr = rc.closest('tr'); tr.classList.add('picked'); tr.querySelectorAll('.rc[form="bulk"]').forEach(function (c) { c.checked = true; }); }
  });
  rowChecks.forEach(function (rc) {
    rc.addEventListener('change', function () {
      var tr = rc.closest('tr');
      tr.querySelectorAll('.rc[form="bulk"]').forEach(function (c) { c.checked = rc.checked; });
      tr.classList.toggle('picked', rc.checked);
      update();
    });
  });
  if (all) all.addEventListener('change', function () {
    var on = all.checked;   // read once: update() resets this box while the rows are being ticked
    rowChecks.forEach(function (rc) { rc.checked = on; rc.dispatchEvent(new Event('change')); });
  });
  async function save(form) {
    if (form.dataset.saving === 'true' || !dirty(form)) return;
    if (!form.reportValidity()) return;
    var status = form.querySelector('.repl-save-status'), input = form.elements.qty, failed = false;
    form.querySelector('button[name=saveButton]').hidden = true; form.dataset.saving = 'true'; input.classList.add('busy'); input.classList.remove('bad'); status.textContent = msg.saving; update();
    try {
      var response = await fetch(form.getAttribute('action'), { method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
      var result = await response.json();
      if (!response.ok) throw new Error(result.error || msg.failed);
      form.dataset.saved = result.quantity;
      status.textContent = msg.saved;
      input.classList.add('ok'); setTimeout(function () { input.classList.remove('ok'); }, 1200);
    } catch (e) {
      failed = true; form.querySelector('button[name=saveButton]').hidden = false; status.textContent = e.message || msg.failed; input.classList.add('bad'); input.title = status.textContent;
    } finally {
      form.dataset.saving = 'false'; input.classList.remove('busy'); update();
      if (!failed && dirty(form)) save(form);                       // typed again while saving
    }
  }
  forms.forEach(function (form) {
    var timer;
    form.addEventListener('input', function () { form.querySelector('.repl-save-status').textContent = ''; form.elements.qty.classList.remove('bad'); clearTimeout(timer); timer = setTimeout(function () { save(form); }, 700); update(); });
    form.addEventListener('change', function () { clearTimeout(timer); save(form); });
    form.addEventListener('submit', function (e) { e.preventDefault(); clearTimeout(timer); save(form); });
  });
  if (bulk) bulk.addEventListener('submit', function (e) {
    update();
    if (document.getElementById('repl-proceed').disabled) { e.preventDefault(); return; }
    var here = {}; recChecks.forEach(function (c) { here[c.value] = true; });
    bulk.querySelectorAll('input[data-kept]').forEach(function (n) { n.remove(); });
    Object.keys(store).forEach(function (k) {   // products ticked on other pages go with this one
      store[k].ids.forEach(function (id) {
        if (here[id]) return;
        var h = document.createElement('input'); h.type = 'hidden'; h.name = 'ids'; h.value = id; h.setAttribute('data-kept', '1'); bulk.appendChild(h);
      });
    });
    store = {}; persist();
  });
  function clearAll() {
    store = {};
    rowChecks.forEach(function (rc) { rc.checked = false; rc.dispatchEvent(new Event('change')); });
    update();
  }
  ['repl-clear', 'rsp-clear'].forEach(function (id) { var b = document.getElementById(id); if (b) b.addEventListener('click', clearAll); });
  function alignDock() {
    if (!bar || bar.hidden) return;
    if (phone.matches) { bar.style.left = ''; bar.style.right = ''; bar.style.transform = ''; return; }
    var wrap = document.querySelector('.rs-wrap');
    if (!wrap) return;
    var r = wrap.getBoundingClientRect();
    var fab = document.querySelector('.chat-fab');
    var maxRight = r.right;
    if (fab) {
      var fr = fab.getBoundingClientRect();
      if (fr.width > 0 && fr.bottom > window.innerHeight - 90) maxRight = Math.min(maxRight, fr.left - 14);
    }
    bar.style.transform = 'none';
    bar.style.right = 'auto';
    var dockW = bar.offsetWidth || 520;
    var minLeft = Math.max(12, Math.round(r.left));
    var centeredLeft = Math.round((r.left + maxRight) / 2 - dockW / 2);
    var left = Math.max(minLeft, Math.min(centeredLeft, Math.round(maxRight - dockW)));
    bar.style.left = left + 'px';
  }
  window.addEventListener('resize', alignDock);
  window.addEventListener('load', alignDock);
  window.addEventListener('pageshow', update);
  window.addEventListener('beforeunload', function (e) {
    if (forms.some(function (f) { return dirty(f) || f.dataset.saving === 'true'; })) { e.preventDefault(); e.returnValue = ''; }
  });
  update();
})();
