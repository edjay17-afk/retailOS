/* Today page: hover (or tap, or Tab to) any small bar to see its day or hour and exact value; the big figures count up once when the page opens. */
(function () {
  'use strict';
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // 1) tooltips on every bar strip
  document.querySelectorAll('.hv').forEach(function (strip) {
    var tip = strip.querySelector('.hv-tip');
    if (!tip) return;
    var bars = Array.prototype.slice.call(strip.querySelectorAll('.hv-bar')), pre = strip.dataset.prefix || '', suf = strip.dataset.suffix || '';
    function show(bar) {
      bars.forEach(function (b) { b.classList.toggle('on', b === bar); });
      strip.classList.add('hot');
      tip.querySelector('b').textContent = bar.dataset.label;
      tip.querySelector('span').textContent = pre + bar.dataset.amount + suf;
      tip.hidden = false;
      var sr = strip.getBoundingClientRect(), br = bar.getBoundingClientRect(), tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(sr.width - tw, br.left - sr.left + br.width / 2 - tw / 2)) + 'px';
      tip.style.bottom = (sr.bottom - br.top + 8) + 'px';
    }
    function hide() { bars.forEach(function (b) { b.classList.remove('on'); }); strip.classList.remove('hot'); tip.hidden = true; }
    bars.forEach(function (b) {
      b.addEventListener('mouseenter', function () { show(b); });
      b.addEventListener('focus', function () { show(b); });
      b.addEventListener('click', function () { show(b); });
      b.addEventListener('blur', hide);
    });
    strip.addEventListener('mouseleave', hide);
  });

  // 2) one count-up when the page opens; the last frame is always the exact text the server sent
  if (still) return;
  var money = new Intl.NumberFormat('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  document.querySelectorAll('#live-sales-today, .store-today').forEach(function (el) {
    var exact = el.textContent, to = Number(exact.replace(/,/g, ''));
    if (!isFinite(to) || to <= 0) return;
    var t0 = null, dur = 700;
    function step(t) {
      if (t0 === null) t0 = t;
      var k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      if (el.textContent !== exact || k >= 1) { if (k >= 1 && el.textContent !== exact && !el.dataset.live) el.textContent = exact; }
      if (!el.dataset.live) el.textContent = k < 1 ? money.format(to * e) : exact;
      if (k < 1 && !el.dataset.live) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  });
})();
