/* A small receipt card at the top centre when a sale is recorded:
     [G]  New sale - Getafe          Cash
          PHP 120.05                 3 items
   Click it (or press Enter) to open that sale's receipt; a burst of sales opens today's transactions list.
   Loaded on every page (base.html) for people who may see sales; the dashboard's dashboard_live.js feeds it there, other pages poll for themselves.
   One to three sales show one card each; more than that become a single "5 new sales" card.
   A card leaves by sliding up and fading while the ones below glide up into its place; pointing at a card keeps it on screen. */
(function () {
  'use strict';
  var messages = {};
  try { messages = JSON.parse(document.getElementById('ui-messages').textContent); } catch (e) { /* English fallback */ }
  function tr(key) { return messages[key] || key; }
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var money = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var STAY = 6500, STAY_AFTER_POINTER = 2500, LEAVE = 380;
  var holder;
  function stack() {
    if (holder) return holder;
    holder = document.createElement('div'); holder.className = 'sale-toasts';
    holder.setAttribute('role', 'status'); holder.setAttribute('aria-live', 'polite');
    document.body.appendChild(holder);
    return holder;
  }
  function span(cls, text) { var s = document.createElement('span'); s.className = cls; if (text !== undefined) s.textContent = text; return s; }
  function units(n) {
    n = Number(n) || 0;
    if (n <= 0) return '';
    var shown = Math.round(n * 10) / 10;
    return shown === 1 ? tr('1 item') : tr('{count} items').replace('{count}', String(shown));
  }

  function leave(el) {
    if (el._leaving) return;
    el._leaving = true; clearTimeout(el._timer);
    if (still) { if (el.parentNode) el.parentNode.removeChild(el); return; }
    el.style.height = el.offsetHeight + 'px';   // pin the height so it can shrink smoothly and the cards below glide up
    void el.offsetHeight;
    el.classList.add('out');
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, LEAVE + 40);
  }
  function stayFor(el, ms) { clearTimeout(el._timer); el._timer = setTimeout(function () { leave(el); }, ms); }

  function card(o) {
    var box = stack(), el = document.createElement('div'); el.className = 'sale-toast';
    var av = span('sale-toast-av', o.badge); av.setAttribute('aria-hidden', 'true');
    var main = span('sale-toast-main');
    var small = document.createElement('small'); small.textContent = o.title;
    var strong = document.createElement('strong'); strong.textContent = o.amount;
    main.appendChild(small); main.appendChild(strong);
    el.appendChild(av); el.appendChild(main);
    if (o.pay || o.items) {
      var side = span('sale-toast-side');
      if (o.pay) { var tag = document.createElement('em'); tag.textContent = tr(o.pay); tag.setAttribute('data-pay', o.pay.toLowerCase()); side.appendChild(tag); }
      if (o.items) side.appendChild(span('sale-toast-items', o.items));
      el.appendChild(side);
    }
    if (o.href) {
      el.classList.add('is-link'); el.setAttribute('role', 'link'); el.tabIndex = 0;
      el.setAttribute('aria-label', o.title + ', ' + o.amount);
      var go = function (e) { if (e.ctrlKey || e.metaKey || e.shiftKey) window.open(o.href, '_blank', 'noopener'); else window.location.assign(o.href); };
      el.addEventListener('click', function (e) { if (!e.target.closest('.sale-toast-x')) go(e); });
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); go(e); } });
    }
    var x = document.createElement('button'); x.type = 'button'; x.className = 'sale-toast-x'; x.setAttribute('aria-label', tr('Close'));
    x.innerHTML = '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>';
    x.addEventListener('click', function (e) { e.stopPropagation(); leave(el); });
    el.appendChild(x);
    el.addEventListener('mouseenter', function () { clearTimeout(el._timer); });
    el.addEventListener('mouseleave', function () { stayFor(el, STAY_AFTER_POINTER); });
    el.addEventListener('focusin', function () { clearTimeout(el._timer); });
    el.addEventListener('focusout', function () { stayFor(el, STAY_AFTER_POINTER); });
    while (box.children.length >= 3) leave(box.firstChild);
    box.appendChild(el);
    stayFor(el, STAY);
  }

  function show(sales, canOpen) {
    if (!sales || !sales.length) return;
    if (sales.length <= 3) {
      sales.forEach(function (s, i) {
        setTimeout(function () {
          card({ badge: (s.store || '?').trim().charAt(0).toUpperCase(), title: tr('New sale') + (s.store ? ' · ' + s.store : ''),
                 amount: 'PHP ' + money.format(s.total), pay: s.pay || '', items: units(s.items), href: canOpen ? '/sales/tx/' + s.id : '' });
        }, i * 450);
      });
      return;
    }
    var sum = sales.reduce(function (n, s) { return n + (Number(s.total) || 0); }, 0);
    var count = sales.reduce(function (n, s) { return n + (Number(s.items) || 0); }, 0);
    card({ badge: String(sales.length), title: tr('{count} new sales').replace('{count}', sales.length), amount: 'PHP ' + money.format(sum), pay: '', items: units(count),
           href: canOpen ? '/sales/transactions' : '' });
  }
  // What the page has already seen is kept for the tab, so a sale recorded while moving between pages still pops up on the next one.
  var KEY = 'ros-sale-seen', FRESH = 5 * 60 * 1000, lastTx = null;
  try { var kept = JSON.parse(sessionStorage.getItem(KEY) || 'null'); if (kept && Number.isFinite(kept.tx) && Date.now() - kept.at < FRESH) lastTx = kept.tx; } catch (e) { /* start from the first read */ }
  function query() { return lastTx === null ? '' : '?after=' + lastTx; }
  function feed(data) {   // the first read only learns the latest sale, so nothing pops up for sales that were already there
    if (lastTx !== null) show(data.new_sales || [], !!data.can_open);
    if (Number.isFinite(data.latest_tx)) lastTx = Math.max(lastTx || 0, data.latest_tx);
    if (lastTx !== null) { try { sessionStorage.setItem(KEY, JSON.stringify({ tx: lastTx, at: Date.now() })); } catch (e) { /* private window */ } }
  }
  window.SaleToast = { show: show, feed: feed, query: query };

  // Every page except the dashboard (whose own live refresh feeds the same cards) checks for new sales every 30 seconds.
  function watch() {
    var timer, busy = false, stopped = false;
    function schedule() { clearTimeout(timer); if (!document.hidden && !stopped) timer = setTimeout(refresh, 30000); }
    async function refresh() {
      if (busy || document.hidden || stopped) return;
      busy = true;
      var controller = new AbortController(), timeout = setTimeout(function () { controller.abort(); }, 15000);
      try {
        var response = await fetch('/dashboard/store-sales' + query(), { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (response.redirected || response.status === 401 || response.status === 403) { stopped = true; return; }
        if (!response.ok) throw new Error();
        var data = await response.json();
        if (data && Array.isArray(data.new_sales)) feed(data);
      } catch (e) { /* quiet: the next check tries again */ }
      finally { clearTimeout(timeout); busy = false; schedule(); }
    }
    document.addEventListener('visibilitychange', function () { if (document.hidden) clearTimeout(timer); else refresh(); });
    window.addEventListener('pagehide', function () { clearTimeout(timer); });
    window.addEventListener('pageshow', schedule);
    refresh(); schedule();
  }
  if (document.body && document.body.hasAttribute('data-sale-alerts') && !document.getElementById('live-store-sales')) watch();
})();
