(function () {
  'use strict';

  var money = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var whole = new Intl.NumberFormat('en-PH', { maximumFractionDigits: 0 });
  var storeData = {};
  try {
    var rawEl = document.getElementById('dashboard-store-data');
    if (rawEl) storeData = JSON.parse(rawEl.textContent || '{}');
  } catch (e) { storeData = {}; }

  // 1) Branch highlight / filter pills
  var branchSeg = document.getElementById('dash-branch-seg');
  if (branchSeg) {
    branchSeg.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-branch]');
      if (!btn) return;
      branchSeg.querySelectorAll('button').forEach(function (b) { b.classList.remove('on'); });
      btn.classList.add('on');
      var sel = btn.getAttribute('data-branch');
      document.querySelectorAll('.b-stores .b-card[data-store-id]').forEach(function (card) {
        card.classList.toggle('is-dimmed', sel !== 'all' && card.getAttribute('data-store-id') !== sel);
      });
    });
  }

  // 2) Priority queue tabs
  var qTabs = document.getElementById('dash-q-tabs');
  if (qTabs) {
    qTabs.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-q]');
      if (!btn) return;
      qTabs.querySelectorAll('button').forEach(function (b) { b.classList.remove('on'); });
      btn.classList.add('on');
      var k = btn.getAttribute('data-q');
      document.querySelectorAll('#dash-q-list .dq-item').forEach(function (item) {
        item.hidden = (k !== 'all' && item.getAttribute('data-cat') !== k);
      });
    });
  }

  // 3) Velocity & Gaps tabs
  var velTabs = document.getElementById('dash-vel-tabs');
  if (velTabs) {
    velTabs.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-v]');
      if (!btn) return;
      velTabs.querySelectorAll('button').forEach(function (b) { b.classList.remove('on'); });
      btn.classList.add('on');
      var v = btn.getAttribute('data-v');
      var bestEl = document.querySelector('[data-live="best"]');
      var gapsEl = document.getElementById('dash-vel-gaps');
      if (bestEl) bestEl.hidden = (v !== 'best');
      if (gapsEl) gapsEl.hidden = (v !== 'gaps');
    });
  }

  // 4) Slide-over Store Pulse Drawer
  var drawer = document.getElementById('dash-store-drawer');
  var scrim = document.getElementById('dash-store-scrim');
  var drClose = document.getElementById('dash-dr-close');
  var drTitle = document.getElementById('dash-dr-title');
  var drSub = document.getElementById('dash-dr-sub');
  var drBody = document.getElementById('dash-dr-body');
  var drSalesLink = document.getElementById('dash-dr-sales');
  var drChecksLink = document.getElementById('dash-dr-checks');
  var openStoreId = null;

  function escHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function renderDrawerBody(s) {
    if (!drBody || !s) return;
    var avg = s.tx > 0 ? s.today / s.tx : 0;
    var barsHtml = (s.bars || []).map(function (b, idx, arr) {
      var cls = idx === arr.length - 1 ? ' class="last"' : '';
      return '<i' + cls + ' title="' + escHtml(b.label) + ': PHP ' + money.format(b.amount || 0) + '" style="height:' + Math.max(4, Math.round(b.h || 0)) + '%"></i>';
    }).join('');
    var checksText = s.checks_total ? ((s.checks_total - (s.checks_left || 0)) + ' / ' + s.checks_total + ' done') : '—';
    var gapText = s.gap ? (s.gap.name + ' (' + s.gap.per_day + '/day)') : 'No out-of-stock 30d seller';
    drBody.innerHTML =
      '<div class="dash-dr-kpis">' +
        '<div class="dash-dr-kpi"><span>Net sales today</span><b id="dash-dr-sales-amt">PHP ' + money.format(s.today || 0) + '</b></div>' +
        '<div class="dash-dr-kpi"><span>Avg basket</span><b id="dash-dr-avg-amt">PHP ' + money.format(avg) + '</b></div>' +
        '<div class="dash-dr-kpi"><span>Checklists today</span><b>' + escHtml(checksText) + '</b></div>' +
        '<div class="dash-dr-kpi"><span>Out of stock</span><b' + (s.out_of_stock ? ' style="color:var(--bad)"' : '') + '>' + whole.format(s.out_of_stock || 0) + ' products</b></div>' +
      '</div>' +
      (barsHtml ? '<div><div class="dash-dr-sec">Last 14 complete days</div><div class="dash-dr-bars">' + barsHtml + '</div></div>' : '') +
      '<div>' +
        '<div class="dash-dr-sec">Branch snapshot</div>' +
        '<div class="dash-dr-list">' +
          '<div class="dash-dr-row"><span>Biggest stockout gap</span><b>' + escHtml(gapText) + '</b></div>' +
          '<div class="dash-dr-row"><span>Auto-replenishment</span><b>' + whole.format(s.orders || 0) + ' items ready</b></div>' +
          '<div class="dash-dr-row"><span>Open issues</span><b>' + whole.format(s.issues || 0) + ' issue(s)</b></div>' +
        '</div>' +
      '</div>';
  }

  function openStoreDrawer(id) {
    var s = storeData[String(id)];
    if (!s || !drawer || !scrim) return;
    openStoreId = String(id);
    if (drTitle) drTitle.textContent = s.name || '';
    if (drSub) {
      var parts = [];
      if (s.reg_text) parts.push(s.reg_text);
      parts.push(whole.format(s.tx || 0) + ' transactions today');
      drSub.textContent = parts.join(' · ');
    }
    renderDrawerBody(s);
    if (drSalesLink) drSalesLink.href = '/sales?loc=' + encodeURIComponent(id) + '&days=1';
    if (drChecksLink) drChecksLink.href = '/inspections';
    scrim.classList.add('open');
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
  }

  function closeDrawer() {
    if (!drawer || !scrim) return;
    openStoreId = null;
    scrim.classList.remove('open');
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
  }

  document.addEventListener('click', function (e) {
    var card = e.target.closest && e.target.closest('.b-stores .b-card[data-store-id]');
    if (!card) return;
    if (window.innerWidth <= 700) return;
    if (e.target.closest('a, button, .hv-bar')) return;
    openStoreDrawer(card.getAttribute('data-store-id'));
  });
  if (scrim) scrim.addEventListener('click', closeDrawer);
  if (drClose) drClose.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && drawer && drawer.classList.contains('open')) closeDrawer();
  });

  // 5) Keep in-memory store snapshot and open drawer synced with 30s live polling
  window.addEventListener('ros:sales-live', function (e) {
    var data = e.detail;
    if (!data || !Array.isArray(data.stores)) return;
    data.stores.forEach(function (st) {
      var key = String(st.id);
      if (storeData[key]) {
        storeData[key].today = st.today;
        storeData[key].yesterday = st.yesterday;
        storeData[key].tx = st.tx;
      }
    });
    if (openStoreId && storeData[openStoreId]) {
      var cur = storeData[openStoreId];
      var salesAmt = document.getElementById('dash-dr-sales-amt');
      var avgAmt = document.getElementById('dash-dr-avg-amt');
      if (salesAmt) salesAmt.textContent = 'PHP ' + money.format(cur.today || 0);
      if (avgAmt) avgAmt.textContent = 'PHP ' + money.format(cur.tx > 0 ? (cur.today / cur.tx) : 0);
      if (drSub && cur.reg_text) drSub.textContent = cur.reg_text + ' · ' + whole.format(cur.tx || 0) + ' transactions today';
    }
  });
})();
