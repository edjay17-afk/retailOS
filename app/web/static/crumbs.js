/* The breadcrumb names the page you came from, and its link takes you back to it (same filters and page), not to a fixed parent.
   The trail lives in this browser tab only (sessionStorage); opening a page by typing its address or from another site starts a new trail. */
(function () {
  'use strict';
  var KEY = 'ros-trail', MAX = 20;
  var navs = document.querySelectorAll('.crumbs');   // the header one (phones) and the one under the store buttons (desktop)
  var nav = navs[0];
  if (!nav) return;
  var curEl = nav.querySelector('.cur');
  if (!curEl) return;
  var here = location.pathname + location.search, label = curEl.textContent.trim();

  function read() { try { var t = JSON.parse(sessionStorage.getItem(KEY) || '[]'); return Array.isArray(t) ? t : []; } catch (e) { return []; } }
  function write(t) { try { sessionStorage.setItem(KEY, JSON.stringify(t.slice(-MAX))); } catch (e) { /* private window: the default breadcrumb stays */ } }
  function refPath() {
    try { var r = new URL(document.referrer); return r.origin === location.origin ? r.pathname + r.search : ''; } catch (e) { return ''; }
  }

  // opening a section from the menu (sidebar, bottom bar, More) is a fresh start, not a step "inside" the page you were on
  var fresh = false;
  try { fresh = sessionStorage.getItem(KEY + '-reset') === '1'; sessionStorage.removeItem(KEY + '-reset'); } catch (e) { /* ignore */ }
  document.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('#sidebar a, .mtabs a, .more-sheet a, .phone-actions a, .fly-menu a')) { try { sessionStorage.setItem(KEY + '-reset', '1'); } catch (x) { /* ignore */ } }
  }, true);

  var trail = read(), top = trail[trail.length - 1], ref = refPath();
  if (fresh) {
    trail = [{ u: here, l: label }];
  } else if (top && top.u === here) {
    top.l = label;                                          // a reload
  } else if (trail.length > 1 && trail[trail.length - 2].u === here) {
    trail.pop(); trail[trail.length - 1].l = label;         // the Back button
  } else if (top && ref && top.u === ref) {
    var samePage = top.u.split('?')[0] === location.pathname;   // a filter or page change of the same list replaces its entry
    if (samePage) trail[trail.length - 1] = { u: here, l: label }; else trail.push({ u: here, l: label });
  } else {
    trail = [{ u: here, l: label }];                        // a new start
  }
  write(trail);

  var prev = trail.length > 1 ? trail[trail.length - 2] : null;
  if (prev && prev.l && prev.l !== label) {
    navs.forEach(function (n) {
      var cur = n.querySelector('.cur');
      if (!cur) return;
      n.textContent = '';
      var a = document.createElement('a'); a.href = prev.u; a.textContent = prev.l;
      var sep = document.createElement('span'); sep.className = 'sep'; sep.setAttribute('aria-hidden', 'true'); sep.textContent = '›';
      n.appendChild(a); n.appendChild(sep); n.appendChild(cur);
    });
  }
})();
