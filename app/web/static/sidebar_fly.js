/* Sidebar: on a computer, a section with several pages (Inventory, Sales ...) opens a small menu beside its own button instead of a drop-down.
   Choosing a page, clicking outside, pressing Esc or choosing the section again closes it. Phones keep the drop-down inside the slide-in menu. */
(function () {
  'use strict';
  var sidebar = document.getElementById('sidebar'), nav = document.getElementById('sidebar-nav'), search = document.getElementById('navigation-search');
  if (!sidebar || !nav) return;
  var desktop = function () { return window.innerWidth > 1000; };
  var groups = Array.prototype.slice.call(nav.querySelectorAll('.nav-group:not(.nav-home)'));
  function close(except) {
    groups.forEach(function (g) {
      if (g === except) return;
      g.classList.remove('fly-open');
      var b = g.querySelector('[data-ng-toggle]');
      if (b && desktop() && !sidebar.classList.contains('is-searching')) b.setAttribute('aria-expanded', 'false');
    });
  }
  function place(g) {
    // the menu sits beside its own button: same top edge, just right of the sidebar, kept on screen
    var panel = g.querySelector('.ng-links'), btn = g.querySelector('[data-ng-toggle]');
    var b = btn.getBoundingClientRect(), side = sidebar.getBoundingClientRect(), h = panel.offsetHeight;
    var top = Math.min(b.top - 4, window.innerHeight - h - 12);
    panel.style.left = Math.round(side.right + 8) + 'px';
    panel.style.top = Math.max(12, Math.round(top)) + 'px';
  }
  // On a computer the section buttons open the panel (this runs before the drop-down code in app.js, which is skipped there).
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('#sidebar-nav [data-ng-toggle]');
    if (btn && desktop() && !sidebar.classList.contains('is-searching')) {
      e.stopImmediatePropagation();
      var g = btn.closest('.nav-group'), open = !g.classList.contains('fly-open');
      close(g);
      g.classList.toggle('fly-open', open);
      btn.setAttribute('aria-expanded', String(open));
      if (open) { place(g); var first = g.querySelector('.ng-links .nl.on, .ng-links .nl'); if (first && e.detail === 0) first.focus(); }
      return;
    }
    var link = e.target.closest('#sidebar-nav .ng-links .nl');
    if (link && desktop()) { close(null); return; }           // a page was chosen: the panel closes (the page opens)
    if (!e.target.closest('#sidebar')) close(null);            // clicked somewhere else
  }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var open = groups.filter(function (g) { return g.classList.contains('fly-open'); })[0];
    if (open) { close(null); var b = open.querySelector('[data-ng-toggle]'); if (b) b.focus(); }
  });
  window.addEventListener('resize', function () { close(null); });
  // while typing in the page search the old inline list is easier to scan, so the panel steps aside
  if (search) search.addEventListener('input', function () { close(null); sidebar.classList.toggle('is-searching', search.value.trim() !== ''); });
  // the current section is shown as the current page by the server; its aria-expanded would say "open" with no panel, so say false on computers
  if (desktop()) groups.forEach(function (g) { var b = g.querySelector('[data-ng-toggle]'); if (b) b.setAttribute('aria-expanded', 'false'); });
})();
