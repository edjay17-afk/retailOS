/* The Today page keeps its checklists, orders, open issues, jobs and warehouse numbers up to date without a reload.
   Every minute (only while the tab is visible) it reads the page again and swaps the parts marked data-live, so nothing else on the screen moves. */
(function () {
  'use strict';
  if (!document.querySelector('[data-live]')) return;
  var timer, busy = false, stopped = false;

  function schedule() { clearTimeout(timer); if (!document.hidden && !stopped) timer = setTimeout(refresh, 60000); }

  function refresh() {
    if (busy || document.hidden || stopped) return;
    busy = true;
    var controller = new AbortController(), timeout = setTimeout(function () { controller.abort(); }, 20000);
    fetch(location.pathname + location.search, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal, headers: { 'X-Requested-With': 'live-refresh' } })
      .then(function (r) {
        if (r.redirected || r.status === 401 || r.status === 403) { stopped = true; throw new Error('signed out'); }
        if (!r.ok) throw new Error('bad status');
        return r.text();
      })
      .then(function (html) {
        var next = new DOMParser().parseFromString(html, 'text/html');
        document.querySelectorAll('[data-live]').forEach(function (el) {
          var fresh = next.querySelector('[data-live="' + el.getAttribute('data-live') + '"]');
          if (fresh && fresh.innerHTML !== el.innerHTML) el.innerHTML = fresh.innerHTML;
        });
      })
      .catch(function () { /* keep what is on screen and try again shortly */ })
      .then(function () { clearTimeout(timeout); busy = false; schedule(); });
  }

  document.addEventListener('visibilitychange', function () { if (document.hidden) clearTimeout(timer); else refresh(); });
  window.addEventListener('pagehide', function () { clearTimeout(timer); });
  window.addEventListener('pageshow', schedule);
  schedule();
})();
