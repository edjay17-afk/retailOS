/* Transactions: a row opens its details in a slide-over on the right instead of a new page.
   Ctrl/Shift/middle-click, or a failed load, still goes to the full page. */
(function () {
  'use strict';
  var drawer = document.getElementById('drawer'), body = document.getElementById('drawer-body'), title = document.getElementById('drawer-title'), scrim = document.getElementById('drawer-scrim');
  if (!drawer || !body) return;
  var seq = 0;
  function open(url, invoice) {
    var mine = ++seq;
    title.textContent = invoice || '';
    body.innerHTML = '<p class="mute" style="padding:18px">…</p>';
    drawer.hidden = false; if (scrim) scrim.hidden = true; document.body.classList.add('drawer-open'); if (!drawer.open) drawer.showModal();
    fetch(url + '/panel', { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        if (mine !== seq) return;
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var panel = doc.querySelector('.txp');
        if (!panel) throw new Error('no details');
        body.innerHTML = '';
        body.appendChild(panel);
      })
      .catch(function () { window.location.href = url; });
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
