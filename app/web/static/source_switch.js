/* Business switch: a white thumb sits behind the chosen business and slides to the one you click before the page changes.
   The buttons stay real form buttons and links, so nothing about switching changes - this only moves the thumb. */
(function () {
  'use strict';
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function settle(box) {
    box.classList.add('no-anim');
    window.requestAnimationFrame(function () { window.requestAnimationFrame(function () { box.classList.remove('no-anim'); }); });
  }
  document.querySelectorAll('.pos-source-switch').forEach(function (box) {
    var busy = false;
    function move(el) { box.style.setProperty('--thumb-x', el.offsetLeft + 'px'); box.style.setProperty('--thumb-w', el.offsetWidth + 'px'); }
    function place() {
      var on = box.querySelector('.is-active');
      if (!on) { box.classList.remove('has-thumb'); return; }
      if (!on.offsetWidth) return;   // hidden at this screen size: placed once it shows
      box.classList.add('has-thumb'); move(on);
    }
    settle(box); place();
    if (window.ResizeObserver) new ResizeObserver(function () { if (!busy) { settle(box); place(); } }).observe(box);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { settle(box); place(); });
    box.addEventListener('click', function (e) {
      var el = e.target.closest('button, a.seven-btn');
      if (!el || !box.contains(el) || busy || still || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      if (el.classList.contains('is-active') || !box.classList.contains('has-thumb')) return;
      e.preventDefault(); busy = true; move(el);
      window.setTimeout(function () {
        if (el.tagName === 'A') window.location.assign(el.href);
        else if (el.form && el.form.requestSubmit) el.form.requestSubmit(el); else el.form.submit();
      }, 240);
      window.addEventListener('pageshow', function () { busy = false; }, { once: true });
    });
  });
})();
