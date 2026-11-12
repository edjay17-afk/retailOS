/* Retail OS dropdown. Every <select> keeps working as before (its value, its form, its change event), but the list that opens
   is ours instead of the browser's: one floating card on a computer, a bottom sheet on a phone.
   Nothing in the templates has to change: a closed select looks the way its page styles it; only the opening is taken over.
   Keyboard: Enter, Space or Up/Down opens; inside the list Up/Down/Home/End move, typing jumps to a match, Enter picks, Esc closes. */
(function () {
  'use strict';
  var pop, scrim, list, sheetTitle, current, items = [], active = -1, typed = '', typedAt = 0, openedBy = 0;
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var phone = window.matchMedia ? window.matchMedia('(max-width: 700px)') : { matches: false };
  var CHECK = '<svg viewBox="0 0 12 12" width="13" height="13" aria-hidden="true"><path d="M2.4 6.4l2.3 2.3 4.9-5.2" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function eligible(el) {
    return el && el.tagName === 'SELECT' && !el.multiple && !(el.size > 1) && !el.disabled && !el.hasAttribute('data-native');
  }
  function build() {
    if (pop) return;
    scrim = document.createElement('div'); scrim.className = 'cs-scrim'; scrim.hidden = true;
    pop = document.createElement('div'); pop.className = 'cs-pop'; pop.hidden = true;
    sheetTitle = document.createElement('div'); sheetTitle.className = 'cs-title';
    list = document.createElement('div'); list.className = 'cs-list'; list.setAttribute('role', 'listbox'); list.tabIndex = -1;
    pop.appendChild(sheetTitle); pop.appendChild(list);
    scrim.addEventListener('mousedown', function (e) { e.preventDefault(); close(false); });
    pop.addEventListener('mousedown', function (e) { e.preventDefault(); });   // keep focus on the select
    list.addEventListener('click', function (e) {
      var row = e.target.closest('.cs-opt'); if (!row || row.getAttribute('aria-disabled') === 'true') return;
      choose(items.indexOf(row));
    });
    list.addEventListener('mousemove', function (e) {
      var row = e.target.closest('.cs-opt'); if (row && row.getAttribute('aria-disabled') !== 'true') mark(items.indexOf(row), false);
    });
  }
  function titleOf(select) {
    var t = select.getAttribute('aria-label');
    if (!t && select.labels && select.labels[0]) { var s = select.labels[0].querySelector('span'); t = (s || select.labels[0]).textContent; }
    return (t || '').replace(/\s+/g, ' ').trim();
  }

  function open(select) {
    build();
    if (current === select && !pop.hidden) { close(true); return; }
    if (current) close(false);
    current = select; items = []; list.textContent = '';
    Array.prototype.forEach.call(select.children, function add(node) {
      if (node.tagName === 'OPTGROUP') {
        var h = document.createElement('div'); h.className = 'cs-group'; h.textContent = node.label; list.appendChild(h);
        Array.prototype.forEach.call(node.children, add); return;
      }
      if (node.tagName !== 'OPTION' || node.hidden) return;
      var row = document.createElement('div'); row.className = 'cs-opt'; row.setAttribute('role', 'option');
      var text = document.createElement('span'); text.className = 'cs-text'; text.textContent = node.textContent.replace(/\s+/g, ' ').trim() || ' ';
      row.appendChild(text);
      var tick = document.createElement('i'); tick.className = 'cs-tick'; tick.innerHTML = CHECK; row.appendChild(tick);
      row._option = node;
      if (node.disabled) row.setAttribute('aria-disabled', 'true');
      var on = node.index === select.selectedIndex;
      row.setAttribute('aria-selected', on ? 'true' : 'false'); if (on) row.classList.add('is-on');
      list.appendChild(row); items.push(row);
    });
    if (!items.length) { current = null; return; }
    var host = select.closest('dialog[open]') || document.body;   // a modal sits above everything outside it
    if (pop.parentNode !== host) { host.appendChild(scrim); host.appendChild(pop); }
    var asSheet = phone.matches;
    pop.classList.toggle('is-sheet', asSheet); scrim.hidden = !asSheet;
    sheetTitle.textContent = titleOf(select); sheetTitle.hidden = !asSheet || !sheetTitle.textContent;
    pop.hidden = false; pop.classList.remove('in');
    select.setAttribute('aria-expanded', 'true');
    var start = items.findIndex(function (r) { return r.classList.contains('is-on'); });
    mark(start >= 0 ? start : first(0, 1), true);
    place(select, asSheet);
    void pop.offsetHeight; pop.classList.add('in');
    openedBy = Date.now();
  }

  function place(select, asSheet) {
    if (asSheet) { pop.style.cssText = ''; return; }
    var r = select.getBoundingClientRect();
    if (r.width < 24) { var p = select.parentElement; if (p) r = p.getBoundingClientRect(); }
    var vw = document.documentElement.clientWidth, vh = window.innerHeight, gap = 6, edge = 10;
    pop.style.cssText = 'left:0;top:0;min-width:' + Math.round(r.width) + 'px;max-width:' + Math.min(420, vw - edge * 2) + 'px;max-height:none';
    var w = pop.offsetWidth, h = pop.offsetHeight;
    var below = vh - r.bottom - gap - edge, above = r.top - gap - edge;
    var up = h > below && above > below;
    var room = Math.max(120, up ? above : below);
    list.style.maxHeight = Math.max(120, Math.min(room, 340) - 12) + 'px';
    h = pop.offsetHeight;
    var left = Math.min(Math.max(edge, r.left), vw - w - edge);
    if (r.right > vw / 2 && r.left + w > vw - edge) left = Math.max(edge, r.right - w);   // a control on the right edge opens inward
    var top = up ? r.top - gap - h : r.bottom + gap;
    pop.style.left = Math.round(left) + 'px'; pop.style.top = Math.round(top) + 'px';
    pop.style.transformOrigin = (up ? 'bottom' : 'top') + ' left';
    pop.classList.toggle('is-up', up);
  }

  function first(from, step) {
    for (var i = from; i >= 0 && i < items.length; i += step) if (items[i].getAttribute('aria-disabled') !== 'true') return i;
    return -1;
  }
  function mark(i, scroll) {
    if (i < 0 || i >= items.length) return;
    if (items[active]) items[active].classList.remove('is-hot');
    active = i; items[i].classList.add('is-hot');
    if (scroll !== false) {
      var row = items[i], top = row.offsetTop, bottom = top + row.offsetHeight;
      if (top < list.scrollTop + 4) list.scrollTop = Math.max(0, top - 4);
      else if (bottom > list.scrollTop + list.clientHeight - 4) list.scrollTop = bottom - list.clientHeight + 4;
    }
  }
  function choose(i) {
    var row = items[i], select = current; if (!row || !select) return;
    var changed = select.selectedIndex !== row._option.index;
    select.selectedIndex = row._option.index;
    close(true);
    if (changed) {
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }
  function close(refocus) {
    if (!pop || pop.hidden) { current = null; return; }
    var select = current; current = null;
    pop.classList.remove('in'); pop.hidden = true; scrim.hidden = true; active = -1;
    if (select) { select.removeAttribute('aria-expanded'); select.removeAttribute('aria-activedescendant'); if (refocus) try { select.focus({ preventScroll: true }); } catch (e) { /* gone */ } }
  }

  // Opening: take over from the browser's own list.
  document.addEventListener('mousedown', function (e) {
    var s = e.target.closest && e.target.closest('select');
    if (!eligible(s) || e.button !== 0) return;
    e.preventDefault(); s.focus({ preventScroll: true }); open(s);
  }, true);
  document.addEventListener('click', function (e) {   // touch screens can open the native list on click
    var s = e.target.closest && e.target.closest('select');
    if (eligible(s)) e.preventDefault();
  }, true);
  document.addEventListener('keydown', function (e) {
    if (current && pop && !pop.hidden) {
      var k = e.key;
      if (k === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); return; }
      if (k === 'Tab') { close(false); return; }
      if (k === 'ArrowDown') { e.preventDefault(); var n = first(active + 1, 1); if (n >= 0) mark(n); return; }
      if (k === 'ArrowUp') { e.preventDefault(); var p = first(active - 1, -1); if (p >= 0) mark(p); return; }
      if (k === 'Home') { e.preventDefault(); mark(first(0, 1)); return; }
      if (k === 'End') { e.preventDefault(); mark(first(items.length - 1, -1)); return; }
      if (k === 'Enter' || k === ' ') { e.preventDefault(); if (k === ' ' && typed) { typed += ' '; return; } choose(active); return; }
      if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        var now = Date.now(); typed = now - typedAt > 700 ? k.toLowerCase() : typed + k.toLowerCase(); typedAt = now;
        for (var i = 0; i < items.length; i++) {
          var at = (active + 1 + i) % items.length;
          if (items[at].getAttribute('aria-disabled') !== 'true' && items[at].textContent.trim().toLowerCase().indexOf(typed) === 0) { mark(at); break; }
        }
        e.preventDefault();
      }
      return;
    }
    var s = e.target;
    if (eligible(s) && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') && !e.ctrlKey && !e.metaKey) {
      if (e.key === 'Enter' && s.form && !s.closest('[data-cs-enter-opens]')) return;   // Enter keeps submitting forms unless asked otherwise
      e.preventDefault(); open(s);
    }
  }, true);

  window.addEventListener('resize', function () { if (current) close(false); });
  window.addEventListener('scroll', function (e) { if (current && !(pop && pop.contains(e.target)) && Date.now() - openedBy > 80) close(false); }, true);
  document.addEventListener('visibilitychange', function () { if (document.hidden) close(false); });
  window.addEventListener('pagehide', function () { close(false); });
  window.RetailSelect = { close: function () { close(false); } };
})();
