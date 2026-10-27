/* Phone shell: the More sheet, collapsible filters, and labelled cells so tables can show as cards. Computers are not affected. */
(function () {
  'use strict';
  var phone = window.matchMedia('(max-width:700px)');
  if (document.getElementById('mtabs')) document.body.classList.add('has-mtabs');

  // ---- More sheet ----
  var sheet = document.getElementById('more-sheet'), scrim = document.getElementById('more-scrim'), openBtn = document.getElementById('mt-more');
  function close() { if (!sheet) return; sheet.hidden = true; scrim.hidden = true; openBtn.setAttribute('aria-expanded', 'false'); document.body.style.overflow = ''; }
  function open() { sheet.hidden = false; scrim.hidden = false; openBtn.setAttribute('aria-expanded', 'true'); document.body.style.overflow = 'hidden'; }
  if (sheet && openBtn) {
    openBtn.addEventListener('click', function () { sheet.hidden ? open() : close(); });
    scrim.addEventListener('click', close);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    sheet.addEventListener('click', function (e) { if (e.target.closest('a')) close(); });
    var ask = document.getElementById('more-ask'), theme = document.getElementById('more-theme');
    if (ask) ask.addEventListener('click', function () { close(); var f = document.getElementById('chat-fab'); if (f) f.click(); });
    if (theme) theme.addEventListener('click', function () { close(); var t = document.getElementById('theme-toggle'); if (t) t.click(); });
    phone.addEventListener('change', function () { if (!phone.matches) close(); });
  }

  // ---- labels for the cells, so a table row can be a card ----
  var KEEP = '.sc,.rs,.repl,.tb-stores,.drp-pop table,[data-keep-table]';
  // a card shows its title plus two details; the rest is on the page you open by tapping the card
  function simplify(tr) {
    if (tr.dataset.simple) return; tr.dataset.simple = '1';
    var tds = Array.prototype.slice.call(tr.children).filter(function (c) { return c.tagName === 'TD'; });
    var shown = 0;
    tds.forEach(function (td, i) {
      if (i === 0) return;
      if (td.querySelector('button,input,select,textarea,form,a.btn') || td.hasAttribute('colspan') || !td.textContent.trim()) return;
      if (shown < 2) shown++; else td.classList.add('mx');
    });
    var a = tr.querySelector('a[href]');
    if (a && !tr.querySelector('button,input,select,textarea,form')) {
      tr.dataset.go = a.getAttribute('href');
      tr.addEventListener('click', function (e) { if (!e.target.closest('a,button,input,label')) location.href = tr.dataset.go; });
    }
  }
  function label(table) {
    if (table.matches(KEEP) || table.closest('.sc-wrap,.rs-wrap')) return;
    var heads = table.querySelectorAll('thead th, tr:first-child th');
    if (!heads.length) return;
    var names = Array.prototype.map.call(table.querySelector('thead tr, tr').children, function (th) { return th.textContent.trim(); });
    table.querySelectorAll('tbody tr, tfoot tr').forEach(function (tr) {
      Array.prototype.forEach.call(tr.children, function (td, i) {
        if (td.tagName === 'TD' && !td.hasAttribute('data-label')) td.setAttribute('data-label', names[i] || '');
      });
      // simplify(tr) is off: hiding cells would hide totals
    });
    table.classList.add('mcards');
  }
  function cards() { document.querySelectorAll('main table').forEach(function (t) { if (phone.matches) label(t); else t.classList.remove('mcards'); }); }

  // ---- filters: keep the search visible, tuck the rest away ----
  function tuck(form) {
    if (form.dataset.mf || form.matches('.sales-filters,.rs-tools')) return;
    var controls = form.querySelectorAll('select,input:not([type=hidden]):not([type=checkbox]),input[type=checkbox]');
    if (controls.length < 3) return;
    var kids = Array.prototype.slice.call(form.children);
    var search = form.querySelector('input[type=search],input[type=text],input:not([type]):not([type=hidden])');
    var keep = search ? kids.filter(function (k) { return k === search || k.contains(search); })[0] : null;
    var rest = kids.filter(function (k) { return k !== keep && !(k.tagName === 'INPUT' && k.type === 'hidden'); });
    if (!rest.length) return;
    form.dataset.mf = '1'; form.classList.add('mf-form');
    var body = document.createElement('div'); body.className = 'mf-body'; body.hidden = true;
    var btn = document.createElement('button'); btn.type = 'button'; btn.className = 'mf-toggle'; btn.setAttribute('aria-expanded', 'false');
    var count = 0;
    rest.forEach(function (k) {
      k.querySelectorAll('select').forEach(function (s) { if (s.selectedIndex > 0 && s.value !== '' && s.value !== '0') count++; });
      k.querySelectorAll('input[type=checkbox]').forEach(function (c) { if (c.checked) count++; });
      k.querySelectorAll('input:not([type=checkbox]):not([type=hidden])').forEach(function (i) { if (i.value && i !== search) count++; });
    });
    var title = document.documentElement.lang === 'zh' ? '筛选' : 'Filters';
    btn.innerHTML = '<span>' + title + (count ? '<em>' + count + '</em>' : '') + '</span>';
    rest.forEach(function (k) { body.appendChild(k); });
    if (keep) keep.after(btn); else form.prepend(btn);
    btn.after(body);
    btn.addEventListener('click', function () { var o = body.hidden; body.hidden = !o; btn.setAttribute('aria-expanded', String(o)); });
  }
  function filters() { if (phone.matches) document.querySelectorAll('main form.filters, main form.sc-filters').forEach(tuck); }

  function init() { cards(); filters(); }
  init();
  phone.addEventListener('change', init);
  // pages that build their rows later (live updates) get their cells labelled too
  new MutationObserver(function () { if (phone.matches) cards(); }).observe(document.querySelector('main') || document.body, { childList: true, subtree: true });
})();
