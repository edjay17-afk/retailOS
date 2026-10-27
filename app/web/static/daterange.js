/* Date-range picker, modelled on StoreHub's: a button with the range, and a panel with two date boxes, a calendar to click a range on, and ready-made ranges.
   <div class="drp" data-from="2026-10-10" data-to="2026-10-16" data-today="2026-10-16" data-i18n='{"today":"Today",...}'> inside a <form> that has hidden inputs days / frm / to.
   Apply fills the hidden inputs (days=custom) and submits the form. Dates are plain YYYY-MM-DD text (UTC arithmetic), so the browser's time zone never shifts a day. */
(function () {
  'use strict';
  var DAY = 86400000;
  function parse(s) { var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ''); return m ? Date.UTC(+m[1], +m[2] - 1, +m[3]) : null; }
  function iso(t) { var d = new Date(t); return d.getUTCFullYear() + '-' + ('0' + (d.getUTCMonth() + 1)).slice(-2) + '-' + ('0' + d.getUTCDate()).slice(-2); }
  function us(t) { var d = new Date(t); return ('0' + (d.getUTCMonth() + 1)).slice(-2) + '/' + ('0' + d.getUTCDate()).slice(-2) + '/' + d.getUTCFullYear(); }
  function fromUS(s) { var m = /^\s*(\d{1,2})\/(\d{1,2})\/(\d{4})\s*$/.exec(s || ''); if (!m) return null; var t = Date.UTC(+m[3], +m[1] - 1, +m[2]), d = new Date(t); return d.getUTCMonth() === +m[1] - 1 && d.getUTCDate() === +m[2] ? t : null; }
  function addMonths(t, n) { var d = new Date(t), y = d.getUTCFullYear(), m = d.getUTCMonth() + n, day = d.getUTCDate(); var last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate(); return Date.UTC(y, m, Math.min(day, last)); }
  function mondayOf(t) { var wd = (new Date(t).getUTCDay() + 6) % 7; return t - wd * DAY; }
  function fmtShort(t, lang) {
    var d = new Date(t);
    if (/^en/.test(lang)) return d.getUTCDate() + ' ' + ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()] + ' ' + d.getUTCFullYear();   // 10 Oct 2026, as StoreHub writes it
    try { return new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(d); } catch (e) { return iso(t); }
  }

  function init(root) {
    var form = root.closest('form'), tx = {};
    try { tx = JSON.parse(root.dataset.i18n || '{}'); } catch (e) {}
    var lang = document.documentElement.lang || 'en';
    var today = parse(root.dataset.today), from = parse(root.dataset.from), to = parse(root.dataset.to);
    var dFrom = from, dTo = to, view = new Date(to || today), vy = view.getUTCFullYear(), vm = view.getUTCMonth(), months = 3, preset = null;
    var PRESETS = [
      ['today', function () { return [today, today]; }], ['yesterday', function () { return [today - DAY, today - DAY]; }], null,
      ['thisWeek', function () { return [mondayOf(today), today]; }], ['lastWeek', function () { var m = mondayOf(today) - 7 * DAY; return [m, m + 6 * DAY]; }],
      ['last7', function () { return [today - 6 * DAY, today]; }],
      ['last14', function () { return [today - 13 * DAY, today]; }], null,
      ['thisMonth', function () { var d = new Date(today); return [Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1), today]; }],
      ['lastMonth', function () { var d = new Date(today), s = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - 1, 1); return [s, Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 0)]; }],
      ['lastN', function () { return [addMonths(today, -months) + DAY, today]; }], null,
      ['thisYear', function () { return [Date.UTC(new Date(today).getUTCFullYear(), 0, 1), today]; }],
      ['lastYear', function () { var y = new Date(today).getUTCFullYear() - 1; return [Date.UTC(y, 0, 1), Date.UTC(y, 11, 31)]; }]
    ];
    function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

    var btn = el('button', 'drp-btn'); btn.type = 'button'; btn.setAttribute('aria-haspopup', 'dialog'); btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = '<svg class="i"><use href="#i-calendar"/></svg><span></span>';
    var label = btn.querySelector('span');
    var pop = el('div', 'drp-pop'); pop.setAttribute('role', 'dialog'); pop.hidden = true;
    root.appendChild(btn); root.appendChild(pop);

    function setLabel() { label.textContent = from && to ? (from === to ? fmtShort(from, lang) : fmtShort(from, lang) + ' – ' + fmtShort(to, lang)) : ''; }

    function matchPreset() {
      preset = null;
      for (var i = 0; i < PRESETS.length; i++) { var p = PRESETS[i]; if (!p) continue; var r = p[1](); if (dFrom === r[0] && dTo === r[1]) { preset = p[0]; return; } }
    }

    function build() {
      pop.textContent = '';
      var left = el('div', 'drp-left'), right = el('div', 'drp-right');
      // the two date boxes
      var boxes = el('div', 'drp-boxes'), a = el('input'), z = el('input');
      a.type = z.type = 'text'; a.inputMode = z.inputMode = 'numeric'; a.placeholder = z.placeholder = 'MM/DD/YYYY'; a.value = dFrom ? us(dFrom) : ''; z.value = dTo ? us(dTo) : '';
      a.setAttribute('aria-label', tx.from || 'From'); z.setAttribute('aria-label', tx.to || 'To');
      boxes.appendChild(a); boxes.appendChild(el('span', 'drp-dash', '–')); boxes.appendChild(z);
      function typed(box, which) {
        box.addEventListener('change', function () {
          var t = fromUS(box.value);
          if (t == null || t > today) { box.classList.add('bad'); error.textContent = 'Enter a valid date on or before today.'; return; }
          box.classList.remove('bad');
          if (which === 'from') { dFrom = t; } else { dTo = t; }
          if (dFrom != null && dTo != null && (dFrom > dTo || dTo - dFrom > 400 * DAY)) { error.textContent = 'From must be on or before To; choose at most 401 days.'; return; }
          var c = new Date(which === 'from' ? dFrom : dTo); vy = c.getUTCFullYear(); vm = c.getUTCMonth(); render();
        });
      }
      var error = el('p', 'drp-error'); error.setAttribute('role', 'alert');
      typed(a, 'from'); typed(z, 'to');
      left.appendChild(boxes); left.appendChild(error);
      // the calendar
      var head = el('div', 'drp-head'), prev = el('button', 'drp-nav', '‹'), next = el('button', 'drp-nav', '›'), ms = el('select'), ys = el('select');
      prev.type = next.type = 'button'; prev.setAttribute('aria-label', '‹'); next.setAttribute('aria-label', '›');
      var f = new Intl.DateTimeFormat(lang, { month: 'long', timeZone: 'UTC' });
      for (var m = 0; m < 12; m++) { var o = el('option', null, f.format(new Date(Date.UTC(2026, m, 1)))); o.value = m; if (m === vm) o.selected = true; ms.appendChild(o); }
      var ty = new Date(today).getUTCFullYear();
      for (var y = ty; y >= 2024; y--) { var oy = el('option', null, String(y)); oy.value = y; if (y === vy) oy.selected = true; ys.appendChild(oy); }
      ms.addEventListener('change', function () { vm = +ms.value; render(); }); ys.addEventListener('change', function () { vy = +ys.value; render(); });
      prev.addEventListener('click', function () { vm--; if (vm < 0) { vm = 11; vy--; } if (vy < 2024) { vy = 2024; vm = 0; } render(); });
      next.addEventListener('click', function () { vm++; if (vm > 11) { vm = 0; vy++; } render(); });
      var td = new Date(today); if (vy > td.getUTCFullYear() || (vy === td.getUTCFullYear() && vm >= td.getUTCMonth())) next.disabled = true;
      head.appendChild(prev); head.appendChild(ms); head.appendChild(ys); head.appendChild(next);
      var grid = el('div', 'drp-grid');
      var wf = new Intl.DateTimeFormat(lang, { weekday: 'short', timeZone: 'UTC' });
      for (var w = 0; w < 7; w++) grid.appendChild(el('span', 'drp-wd', wf.format(new Date(Date.UTC(2026, 9, 5 + w)))));
      var first = Date.UTC(vy, vm, 1), start = mondayOf(first);
      for (var i = 0; i < 42; i++) {
        var t = start + i * DAY, d = new Date(t), c = el('button', 'drp-day', String(d.getUTCDate())); c.type = 'button';
        var inMonth = d.getUTCMonth() === vm; if (!inMonth) c.classList.add('out');
        if (t > today) { c.disabled = true; c.classList.add('future'); }
        if (dFrom != null && dTo != null && t >= dFrom && t <= dTo) c.classList.add('in');
        if (t === dFrom) c.classList.add('edge', 'edge-l'); if (t === dTo) c.classList.add('edge', 'edge-r');
        if (t === today) c.classList.add('today');
        c.setAttribute('aria-label', us(t));
        (function (t) { c.addEventListener('click', function () {
          if (!picking) { dFrom = t; dTo = null; picking = true; }            // the first click starts a range
          else { if (t < dFrom) { dTo = dFrom; dFrom = t; } else dTo = t; picking = false; }   // the second click ends it
          var cv = new Date(t); vy = cv.getUTCFullYear(); vm = cv.getUTCMonth(); render();
        }); })(t);
        grid.appendChild(c);
      }
      left.appendChild(head); left.appendChild(grid);
      // ready-made ranges
      matchPreset();
      PRESETS.forEach(function (p) {
        if (!p) { right.appendChild(el('div', 'drp-gap')); return; }
        var row = el('label', 'drp-opt'), r = el('input'); r.type = 'radio'; r.checked = preset === p[0];  // no name: the form must not send it
        row.appendChild(r);
        if (p[0] === 'lastN') {
          row.appendChild(el('span', null, tx.last || 'Last'));
          var n = el('input', 'drp-n'); n.type = 'number'; n.min = 1; n.max = 24; n.value = months;
          n.addEventListener('click', function (e) { e.stopPropagation(); });
          n.addEventListener('change', function () { months = Math.max(1, Math.min(24, parseInt(n.value, 10) || 3)); n.value = months; var rg = p[1](); dFrom = rg[0]; dTo = rg[1]; picking = false; var cv = new Date(dTo); vy = cv.getUTCFullYear(); vm = cv.getUTCMonth(); render(); });
          row.appendChild(n); row.appendChild(el('span', null, tx.months || 'Months'));
        } else row.appendChild(el('span', null, tx[p[0]] || p[0]));
        r.addEventListener('change', function () { var rg = p[1](); dFrom = rg[0]; dTo = rg[1]; picking = false; var cv = new Date(dTo); vy = cv.getUTCFullYear(); vm = cv.getUTCMonth(); render(); });
        right.appendChild(row);
      });
      var body = el('div', 'drp-body'); body.appendChild(left); body.appendChild(right);
      var foot = el('div', 'drp-foot'), cancel = el('button', 'sec', tx.cancel || 'Cancel'), ok = el('button', null, tx.apply || 'Apply');
      cancel.type = ok.type = 'button';
      cancel.addEventListener('click', close);
      ok.addEventListener('click', function () {
        var enteredFrom = fromUS(a.value), enteredTo = fromUS(z.value || a.value);
        if (enteredFrom == null || enteredTo == null || enteredFrom > enteredTo || enteredTo > today || enteredTo - enteredFrom > 400 * DAY) { error.textContent = 'Invalid date range. Check both dates, their order, and the 401-day limit. Future dates are not allowed.'; return; }
        dFrom = enteredFrom; dTo = enteredTo;
        form.elements.days.value = 'custom'; form.elements.frm.value = iso(dFrom); form.elements.to.value = iso(dTo); form.submit();
      });
      foot.appendChild(cancel); foot.appendChild(ok);
      pop.appendChild(body); pop.appendChild(foot);
    }
    var picking = false;
    function render() { build(); place(); }
    // keep the whole panel on screen: slide it left when it would run past the right edge, and let it scroll when the window is short
    function place() {
      var stacked = window.innerWidth <= 900;
      pop.style.left = ''; pop.style.maxHeight = '';
      var r = pop.getBoundingClientRect(), shift = Math.min(0, window.innerWidth - 12 - r.right);
      if (r.left + shift < 12) shift = 12 - r.left;
      if (shift) pop.style.left = shift + 'px';
      if (!stacked) pop.style.maxHeight = Math.max(320, window.innerHeight - pop.getBoundingClientRect().top - 12) + 'px';
    }
    function open() { dFrom = from; dTo = to; picking = false; var c = new Date(to || today); vy = c.getUTCFullYear(); vm = c.getUTCMonth(); build(); pop.hidden = false; btn.setAttribute('aria-expanded', 'true'); place(); }
    window.addEventListener('resize', function () { if (!pop.hidden) place(); });
    function close() { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); }
    btn.addEventListener('click', function () { pop.hidden ? open() : close(); });
    document.addEventListener('mousedown', function (e) { if (!pop.hidden && !root.contains(e.target)) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !pop.hidden) { close(); btn.focus(); } });
    setLabel();
  }
  document.querySelectorAll('.drp').forEach(init);
})();
