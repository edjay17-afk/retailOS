/* Tabs that switch in place: clicking a tab slides the highlight to it and swaps only the content below the tab bar,
   with no full page reload. Works for every .tabs bar (Open/Closed/All, Settings sections...). Anything unexpected falls back to a normal page load. */
(function () {
  var main = document.getElementById('main-content');
  if (!main || !window.fetch || !window.DOMParser || !main.animate) return;
  var seq = 0;

  // ---- the sliding highlight ----
  function place(tabs, animate) {
    var pill = tabs.querySelector('.tab-pill'), ons = tabs.querySelectorAll('a.on'), on = ons[0];
    if (!pill) return;
    // one active tab: the highlight glides to it. Two active at once (a filter switched on beside the view) keep the plain per-tab highlight.
    if (ons.length !== 1) { tabs.classList.remove('has-pill'); pill.style.width = '0px'; return; }
    tabs.classList.add('has-pill');
    if (!animate) pill.style.transition = 'none';
    pill.style.width = on.offsetWidth + 'px';
    pill.style.height = on.offsetHeight + 'px';
    pill.style.transform = 'translate(' + on.offsetLeft + 'px,' + on.offsetTop + 'px)';
    if (!animate) { pill.getBoundingClientRect(); pill.style.transition = ''; }
  }
  function setup(tabs) {
    if (tabs.querySelector('.tab-pill') || !tabs.querySelector('a')) return;
    var pill = document.createElement('span');
    pill.className = 'tab-pill'; pill.setAttribute('aria-hidden', 'true');
    tabs.insertBefore(pill, tabs.firstChild);
    place(tabs, false);
  }
  function setupAll(scope) { scope.querySelectorAll('.tabs').forEach(setup); }
  setupAll(document);
  window.addEventListener('resize', function () { document.querySelectorAll('.tabs').forEach(function (t) { place(t, false); }); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { document.querySelectorAll('.tabs').forEach(function (t) { place(t, false); }); });

  // ---- swapping the content ----
  function hardGo(url) { window.location.href = url; }
  function laterNodes(el) {   // everything that comes after the tab bar on the page (the table, filters, notes...)
    var out = [];
    for (var node = el; node && node !== main; node = node.parentElement) {
      for (var sib = node.nextElementSibling; sib; sib = sib.nextElementSibling) out.push(sib);
    }
    return out;
  }
  function runNewScripts(doc) {   // a page-specific script the current page does not have yet (never one that already runs here)
    var have = {}; document.querySelectorAll('script').forEach(function (s) { have[s.src || ('inline:' + s.textContent.trim())] = 1; });
    doc.querySelectorAll('script').forEach(function (s) {
      var key = s.src || ('inline:' + s.textContent.trim());
      if (have[key] || (!s.src && !s.textContent.trim())) return;
      var n = document.createElement('script');
      if (s.src) n.src = s.src; else n.textContent = s.textContent;
      document.body.appendChild(n); have[key] = 1;
    });
  }
  function swap(url, tabs, push, token) {
    var bar = main.querySelector('.tabs');
    return fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'tabswap' } }).then(function (r) {
      if (token !== seq) return null;                      // a newer click already took over
      if (!r.ok || r.redirected && /\/login/.test(r.url)) { hardGo(url); return null; }
      return r.text();
    }).then(function (html) {
      if (html === null || token !== seq) return;
      var doc = new DOMParser().parseFromString(html, 'text/html'), nmain = doc.getElementById('main-content');
      var nbar = nmain && nmain.querySelector('.tabs');
      if (!nmain || !nbar || !bar || nbar.querySelectorAll('a').length !== bar.querySelectorAll('a').length) { hardGo(url); return; }
      runNewScripts(doc);
      // the live tab bar stays where it is (so its highlight can glide); only its links and active tab are brought up to date
      var nlinks = nbar.querySelectorAll('a');
      bar.querySelectorAll('a').forEach(function (a, i) { a.setAttribute('href', nlinks[i].getAttribute('href')); a.classList.toggle('on', nlinks[i].classList.contains('on')); });
      var frag = document.createDocumentFragment();
      Array.prototype.forEach.call(nmain.childNodes, function (n) { frag.appendChild(document.importNode(n, true)); });
      var spot = frag.querySelector('.tabs');
      spot.parentNode.replaceChild(bar, spot);             // moves the live bar into the new layout
      main.classList.add('tab-swap');
      main.replaceChildren(frag);
      laterNodes(bar).forEach(function (n) { n.classList.add('tab-in'); });
      if (doc.title) document.title = doc.title;
      if (push) { history.replaceState({ tabs: 1 }, '', location.href); history.pushState({ tabs: 1 }, '', url); }
      if (window.RetailOSEnhance) window.RetailOSEnhance(main);
      place(bar, true);
      setTimeout(function () { main.classList.remove('tab-swap'); laterNodes(bar).forEach(function (n) { n.classList.remove('tab-in'); }); }, 400);
    }).catch(function () { if (token === seq) hardGo(url); });
  }

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('.tabs a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var tabs = a.closest('.tabs');
    if (a.target || a.hasAttribute('download') || a.origin !== window.location.origin || !tabs.querySelector('.tab-pill')) return;
    e.preventDefault();
    if (a.classList.contains('on') && a.pathname + a.search === window.location.pathname + window.location.search) return;   // already here
    tabs.querySelectorAll('a').forEach(function (x) { x.classList.toggle('on', x === a); });
    place(tabs, true);                                     // the highlight starts gliding at once; the content follows when it arrives
    swap(a.href, tabs, true, ++seq);
  });
  window.addEventListener('popstate', function (e) {       // back / forward between tab states
    if (!(e.state && e.state.tabs) && !main.querySelector('.tabs')) return;
    var tabs = main.querySelector('.tabs');
    if (!tabs) return hardGo(window.location.href);
    swap(window.location.href, tabs, false, ++seq);
  });
})();
