/* Transactions: a row opens its details in a slide-over on the right instead of a new page.
   Ctrl/Shift/middle-click, or a failed load, still goes to the full page. */
(function () {
  'use strict';
  var drawer = document.getElementById('drawer'), body = document.getElementById('drawer-body'), title = document.getElementById('drawer-title'), scrim = document.getElementById('drawer-scrim');
  if (!drawer || !body) return;
  var seq = 0;
  // Only the current, still-open request may paint or navigate. Closing is a
  // transition too: late failures must not take the user away from the list.
  function state(value) {
    drawer.dataset.state = value;
    body.setAttribute('aria-busy', String(value === 'loading'));
  }
  function current(request) { return request === seq && drawer.open && !drawer.hidden; }
  state('idle');
  drawer.addEventListener('close', function () { ++seq; state('idle'); });
  function open(url, invoice) {
    var mine = ++seq;
    title.textContent = invoice || '';
    body.innerHTML = '<p class="mute" style="padding:18px">…</p>';
    drawer.hidden = false; if (scrim) scrim.hidden = true; document.body.classList.add('drawer-open'); if (!drawer.open) drawer.showModal();
    state('loading');
    fetch(url + '/panel', { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        if (!current(mine)) return;
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var panel = doc.querySelector('.txp');
        if (!panel) throw new Error('no details');
        body.innerHTML = '';
        body.appendChild(panel);
        state('ready');
      })
      .catch(function () {
        if (!current(mine)) return;
        state('error');
        window.location.href = url;
      });
  }
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var tr = e.target.closest && e.target.closest('tr[data-href^="/sales/tx/"]');
    if (!tr || e.target.closest('a,button,input,select,label')) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var cells = tr.querySelectorAll('td');
    open(tr.getAttribute('data-href'), cells[1] ? cells[1].textContent.trim() : '');
    tr.classList.add('tx-on'); [].forEach.call(document.querySelectorAll('tr.tx-on'), function (o) { if (o !== tr) o.classList.remove('tx-on'); });
  }, true);
})();
