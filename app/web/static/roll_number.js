/* Rolling numbers: each digit is a small column of 0-9 that slides UP to the new digit, like an odometer.
   RollNumber.set(el, "4,682.00")                  - the digits that changed roll up (a carry, 9 -> 0, keeps rolling up)
   RollNumber.set(el, "4,682.00", {fromZero: true}) - roll up from zeros, used when the page opens
   The exact text is always kept for screen readers and for copying; under "reduce motion" the number just changes. */
(function () {
  'use strict';
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var EM = 1.2, DUR = 900, STAGGER = 55;

  function strip() {
    var s = document.createElement('span'); s.className = 'rn-strip';
    for (var i = 0; i < 20; i++) { var d = document.createElement('span'); d.textContent = String(i % 10); s.appendChild(d); }
    return s;
  }
  function place(col, pos, animate) {
    col.strip.style.transition = animate ? '' : 'none';
    col.strip.style.transform = 'translateY(' + (-pos * EM) + 'em)';
    if (!animate) { void col.strip.offsetHeight; col.strip.style.transition = ''; }
  }
  function shape(text) { return text.replace(/\d/g, '#'); }

  function build(el, text) {
    var sr = document.createElement('span'); sr.className = 'rn-sr'; sr.textContent = text;
    var box = document.createElement('span'); box.className = 'rn'; box.setAttribute('aria-hidden', 'true');
    var cols = [];
    text.split('').forEach(function (ch) {
      var cell = document.createElement('span');
      if (/\d/.test(ch)) {
        cell.className = 'rn-col';
        var s = strip(); cell.appendChild(s);
        var col = { strip: s, pos: Number(ch) }; cols.push(col); place(col, col.pos, false);
      } else { cell.className = 'rn-sep'; cell.textContent = ch; }
      box.appendChild(cell);
    });
    el.textContent = ''; el.appendChild(sr); el.appendChild(box);
    el._rn = { text: text, cols: cols, shape: shape(text), timers: [] };
  }

  function settle(st) {   // after a roll: back to the 0-9 range without a visible jump
    st.timers.forEach(clearTimeout); st.timers = [];
    st.cols.forEach(function (c) { if (c.pos > 9) { c.pos = c.pos % 10; place(c, c.pos, false); } });
  }

  function set(el, text, opts) {
    if (!el) return;
    text = String(text);
    var st = el._rn;
    if (opts && opts.fromZero && !st && !still && /[1-9]/.test(text)) {
      build(el, text.replace(/\d/g, '0'));
      window.requestAnimationFrame(function () { window.requestAnimationFrame(function () { set(el, text); }); });
      return;
    }
    if (!st || st.shape !== shape(text)) { build(el, text); return; }   // first time, or the number got longer or shorter
    if (st.text === text) return;
    settle(st);
    var digits = text.replace(/\D/g, '').split('').map(Number);
    var changed = 0;
    for (var i = st.cols.length - 1; i >= 0; i--) {   // right to left: the cents move first
      var c = st.cols[i], to = digits[i], delta = (to - c.pos + 10) % 10;
      if (!delta) continue;
      c.pos += delta;
      if (still) { c.pos = to; place(c, c.pos, false); continue; }
      (function (col, wait) {
        var go = function () { place(col, col.pos, true); };
        wait ? st.timers.push(setTimeout(go, wait)) : go();
      })(c, changed * STAGGER);
      changed++;
    }
    st.text = text;
    el.querySelector('.rn-sr').textContent = text;
    if (changed && !still) st.timers.push(setTimeout(function () { settle(st); }, DUR + changed * STAGGER + 80));
  }
  window.RollNumber = { set: set };
})();
