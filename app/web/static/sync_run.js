/* Sync > Run now: starts the job(s) and shows each step in a pop-up as it waits, runs, finishes or fails. */
(function () {
  'use strict';
  var form = document.getElementById('run-form'), panel = document.getElementById('run-panel');
  if (!form || !panel || !window.fetch) return;
  var T = document.currentScript.dataset, btn = document.getElementById('run-btn'), list = document.getElementById('run-steps'),
      title = document.getElementById('run-title'), sub = document.getElementById('run-sub'), foot = document.getElementById('run-foot'), fill = document.getElementById('run-fill'), timer = null, token = null, misses = 0;
  function render(s) {
    var done = s.steps.filter(function (x) { return x.status === 'ok' || x.status === 'failed'; }).length, failed = s.steps.filter(function (x) { return x.status === 'failed'; }).length;
    panel.dataset.state = s.done ? (failed ? 'bad' : 'ok') : 'run';
    title.textContent = s.done ? (failed ? T.failed : T.done) : T.running + ' ' + (s.steps.length === 1 ? s.steps[0].label : form.elements.job.selectedOptions[0].textContent);
    sub.textContent = done + ' ' + T.of + ' ' + s.steps.length + ' ' + T.steps + (s.elapsed ? ' \u00b7 ' + s.elapsed + 's' : '');
    fill.style.width = (s.steps.length ? Math.round(100 * (s.done ? 1 : (done + 0.4 * s.steps.some(function (x) { return x.status === 'running'; })) / s.steps.length)) : 0) + '%';
    list.textContent = '';
    s.steps.forEach(function (x) {
      var li = document.createElement('li'), ic = document.createElement('div'), nm = document.createElement('div'), b = document.createElement('b'), tm = document.createElement('div');
      li.className = x.status === 'ok' ? 'ok' : x.status === 'failed' ? 'bad' : x.status === 'running' ? 'run' : '';
      ic.className = 'ic'; ic.textContent = x.status === 'ok' ? '\u2713' : x.status === 'failed' ? '!' : '';
      nm.className = 'nm'; b.textContent = x.label; nm.appendChild(b);
      if (x.status === 'failed' && x.error) { var er = document.createElement('span'); er.textContent = x.error; nm.appendChild(er); }
      tm.className = 'tm'; tm.textContent = x.status === 'ok' ? (x.seconds != null ? x.seconds + 's' : T.completed) : x.status === 'running' ? T.running + '\u2026' : x.status === 'waiting' ? T.waiting : '';
      li.appendChild(ic); li.appendChild(nm); li.appendChild(tm); list.appendChild(li);
    });
    foot.hidden = !s.done;
    if (s.done) { btn.disabled = false; clearInterval(timer); timer = null; }
  }
  function poll() {
    fetch('/sync/run/' + token, { credentials: 'same-origin' }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (s) { misses = 0; render(s); })
      .catch(function () { if (++misses > 5) { clearInterval(timer); timer = null; btn.disabled = false; sub.textContent = T.lost; } });
  }
  form.addEventListener('submit', function (e) {
    e.preventDefault(); btn.disabled = true; if (!panel.open) panel.showModal();
    panel.dataset.state = 'run'; title.textContent = T.running + '\u2026'; sub.textContent = ''; list.textContent = ''; foot.hidden = true; fill.style.width = '0%';
    fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (s) { token = s.token; render(s); if (!s.done) timer = setInterval(poll, 1200); })
      .catch(function () { btn.disabled = false; panel.dataset.state = 'bad'; title.textContent = T.failed; sub.textContent = T.lost; });
  });
  function close() { panel.close(); }
  document.getElementById('run-close').addEventListener('click', close);
  document.getElementById('run-done').addEventListener('click', close);
  panel.addEventListener('click', function (e) { if (e.target === panel) close(); });
  document.getElementById('run-reload').addEventListener('click', function () { location.reload(); });
})();
