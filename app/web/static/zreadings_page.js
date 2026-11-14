/* Live 80mm thermal receipt inspector & interim visibility filter for /sales/zreadings */
(function () {
  'use strict';

  var dataEl = document.getElementById('zr-preview-data');
  var previews = {};
  if (dataEl) {
    try { previews = JSON.parse(dataEl.textContent || '{}'); } catch (e) { previews = {}; }
  }

  function dictFrom(pairs) {
    var out = {};
    (pairs || []).forEach(function (pair) {
      if (pair && pair.length >= 2) out[pair[0]] = pair[1];
    });
    return out;
  }

  function renderKvList(containerId, items) {
    var box = document.getElementById(containerId);
    if (!box) return;
    box.textContent = '';
    items.forEach(function (item) {
      if (!item.value) return;
      var row = document.createElement('div');
      row.className = 'r80-row' + (item.bold ? ' r80-b' : '');
      var k = document.createElement('span');
      k.textContent = item.label;
      var v = document.createElement('span');
      v.textContent = item.value;
      row.appendChild(k);
      row.appendChild(v);
      box.appendChild(row);
    });
  }

  function setText(id, val) {
    var el = document.getElementById(id);
    if (el) el.textContent = val || '';
  }

  function showPreview(shId) {
    var p = previews[shId];
    if (!p) return;

    document.querySelectorAll('tr[data-zread-id]').forEach(function (tr) {
      var active = tr.getAttribute('data-zread-id') === shId;
      tr.classList.toggle('is-inspecting', active);
      var btn = tr.querySelector('[data-zr-inspect]');
      if (btn) {
        btn.classList.toggle('is-active', active);
        btn.textContent = active ? btn.getAttribute('data-lbl-on') : btn.getAttribute('data-lbl-off');
      }
    });

    setText('zr-slip-chip', 'Z #' + p.z_count + ' · ' + p.close_at.slice(0, 5) + ' ' + p.close_at.slice(11));

    var ready = document.getElementById('zr-receipt-ready');
    var pending = document.getElementById('zr-receipt-pending');
    if (!p.detail_ok || !p.receipt) {
      if (ready) ready.hidden = true;
      if (pending) pending.hidden = false;
      setText('zr-p-title', 'Z #' + p.z_count + ' · ' + p.store);
      var pd = document.getElementById('zr-p-detail');
      if (pd) pd.href = '/sales/zreadings/' + encodeURIComponent(shId);
      return;
    }

    if (ready) ready.hidden = false;
    if (pending) pending.hidden = true;

    var d = p.receipt;
    var counters = dictFrom(d.counters);
    var breakdown = dictFrom(d.breakdown);
    var txn = dictFrom(d.transaction);

    setText('zr-r-store', d.store || p.store);
    setText('zr-r-op', d.operatedBy ? 'Operated by: ' + d.operatedBy : '');
    var idParts = [];
    if (d.min) idParts.push('MIN: ' + d.min);
    if (d.ptu) idParts.push('PTU: ' + d.ptu);
    if (d.sn) idParts.push('S/N: ' + d.sn);
    setText('zr-r-ids', idParts.join(' · '));
    setText('zr-r-dt', ((d.reportDate || '') + ' ' + (d.reportTime || '')).trim() || p.close_at);
    setText('zr-r-si', (counters['Beg. SI #'] || '—') + ' — ' + (counters['End. SI #'] || '—'));
    setText('zr-r-zsc', (counters['Z Count No.:'] || p.z_count) + ' / ' + (counters['SALES Count'] || p.tx_count));

    renderKvList('zr-r-breakdown', ['VATABLE SALES', 'VAT AMOUNT', 'VAT EXEMPT SALES', 'Gross Amount', 'Less Discount', 'Net Amount'].map(function (lbl) {
      return { label: lbl + ':', value: breakdown[lbl] || '', bold: lbl === 'Net Amount' };
    }));

    var collItems = (d.collections || []).map(function (c) {
      return { label: c.method + (c.count ? ' (' + c.count + ')' : ''), value: c.amount, bold: false };
    });
    if (d.collectionTotal && d.collectionTotal.amount) {
      collItems.push({
        label: 'Total Collection' + (d.collectionTotal.count ? ' (' + d.collectionTotal.count + ')' : '') + ':',
        value: d.collectionTotal.amount,
        bold: true
      });
    }
    renderKvList('zr-r-collections', collItems);

    renderKvList('zr-r-txn', ['Opening Fund', 'Cash in Drawer:', 'GCASH:', 'Less Withdrawal', 'Expected Drawer', 'Short/Over'].map(function (lbl) {
      return {
        label: lbl.replace(/:$/, '') + ':',
        value: txn[lbl] || '',
        bold: lbl === 'Expected Drawer' || lbl === 'Short/Over'
      };
    }));

    var printBtn = document.getElementById('zr-r-print');
    if (printBtn) printBtn.href = '/sales/zreadings/' + encodeURIComponent(shId) + '/receipt';
    var detailBtn = document.getElementById('zr-r-detail');
    if (detailBtn) detailBtn.href = '/sales/zreadings/' + encodeURIComponent(shId);
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-zr-inspect]');
    if (!btn) return;
    e.preventDefault();
    showPreview(btn.getAttribute('data-zr-inspect'));
  });

  document.querySelectorAll('[data-zr-scope]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var mode = btn.getAttribute('data-zr-scope');
      document.querySelectorAll('[data-zr-scope]').forEach(function (b) {
        b.classList.toggle('on', b === btn);
      });
      document.querySelectorAll('tr.zr-interim').forEach(function (tr) {
        tr.hidden = (mode === 'final');
      });
    });
  });
})();
