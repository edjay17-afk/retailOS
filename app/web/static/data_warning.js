/* The "Data warning" banner clears itself once the data is synced: it is re-read every 30 seconds while the tab is visible. */
(function () {
  'use strict';
  var host = document.getElementById('data-warning');
  if (!host) return;
  var timer, busy = false, stopped = false;
  function schedule() { clearTimeout(timer); if (!document.hidden && !stopped) timer = setTimeout(refresh, 30000); }
  function refresh() {
    if (busy || document.hidden || stopped) return;
    busy = true;
    fetch('/data-warning', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { if (r.redirected || r.status === 401 || r.status === 403) { stopped = true; throw new Error('signed out'); } if (!r.ok) throw new Error('bad'); return r.text(); })
      .then(function (html) { if (html.trim() !== host.innerHTML.trim()) host.innerHTML = html; })
      .catch(function () { /* keep what is shown */ })
      .then(function () { busy = false; schedule(); });
  }
  document.addEventListener('visibilitychange', function () { if (document.hidden) clearTimeout(timer); else refresh(); });
  window.addEventListener('pageshow', schedule);
  schedule();
})();
