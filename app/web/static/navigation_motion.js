(function () {
  'use strict';
  var sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)'), pending = false;
  function plainClick(e, link) {
    return !e.defaultPrevented && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && !link.target && !link.hasAttribute('download');
  }
  document.addEventListener('click', function (e) {
    var link = e.target.closest('#sidebar-nav a.nl, .sidebar-language a');
    if (!link || !plainClick(e, link) || reduce.matches) return;
    if (window.innerWidth > 1000 && link.closest('.nav-group:not(.nav-home) .ng-links')) return;   // pages in the side panel just open; the panel closes with the page
    var url = new URL(link.href, location.href);
    if (url.origin !== location.origin) return;
    var language = link.closest('.sidebar-language');
    if (language && link.classList.contains('on')) return;
    if (!language && url.href === location.href) return;
    if (pending) { e.preventDefault(); return; }
    e.preventDefault(); pending = true;
    var pill;
    if (language) {
      language.dataset.target = link.hreflang;
    } else {
      var group = link.closest('.nav-group');
      var destination = group.querySelector('.ng') || link;
      var source = sidebar.querySelector('.current-group > .ng, .nav-home a.on');
      if (group.classList.contains('current-group')) {
        source = group.querySelector('a.on') || source;
        destination = link;
      }
      if (source && source.getClientRects().length && destination.getClientRects().length) {
        var from = source.getBoundingClientRect(), to = destination.getBoundingClientRect(), box = sidebar.getBoundingClientRect();
        pill = document.createElement('span'); pill.className = 'navigation-moving-highlight'; pill.setAttribute('aria-hidden','true');
        pill.style.cssText = 'left:'+(to.left-box.left)+'px;top:'+(to.top-box.top)+'px;width:'+to.width+'px;height:'+to.height+'px;background:'+getComputedStyle(source).backgroundColor;
        sidebar.appendChild(pill); source.classList.add('navigation-motion-source');
        pill.animate([
          {transform:'translate('+(from.left-to.left)+'px,'+(from.top-to.top)+'px) scale('+from.width/to.width+','+from.height/to.height+')',opacity:0.85},
          {transform:'translate(0,0) scale(1,1)',opacity:0.85}
        ], {duration:220,easing:'cubic-bezier(.22,.7,.25,1)',fill:'forwards'});
      }
    }
    window.setTimeout(function () {
      // Normal document navigation keeps server permissions and unsaved-form guards intact.
      location.assign(url.href);
      window.setTimeout(reset, 600);
    }, 230);
    function reset() {
      pending = false;
      if (pill) pill.remove();
      if (language) delete language.dataset.target;
      sidebar.querySelectorAll('.navigation-motion-source').forEach(function(el){el.classList.remove('navigation-motion-source');});
    }
    window.addEventListener('pageshow', reset, {once:true});
  });
})();
