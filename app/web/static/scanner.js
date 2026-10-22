/* Handheld barcode / QR scanner support (USB, Bluetooth or the dock type). These scanners behave like a very fast keyboard:
   they "type" the code and usually press Enter. This script makes that work anywhere on the page:
   - the scan box always gets the scan, even when nothing is focused (scan the next product without touching the screen);
   - a scan that ends without Enter is submitted by itself after a short pause;
   - a human typing slowly in the box is left alone (press Enter or the button as usual).
   Mark the scan box with data-scanner and it will be used. */
(function () {
  var box = document.querySelector("input[data-scanner]");
  if (!box) return;
  var GAP = 60, IDLE = 160, MIN = 6;       // ms between scanner keys, ms of silence that ends a code, shortest code
  var buf = "", times = [], timer = null;
  var status = document.getElementById("scanner-status");

  function editable(el) {
    if (!el || el === document.body) return false;
    var t = (el.tagName || "").toLowerCase();
    return t === "input" || t === "textarea" || t === "select" || el.isContentEditable;
  }
  function fast() {   // every key so far came faster than a person types
    if (times.length < 2) return true;
    for (var i = 1; i < times.length; i++) if (times[i] - times[i - 1] > GAP) return false;
    return true;
  }
  function reset() { buf = ""; times = []; if (timer) { clearTimeout(timer); timer = null; } }
  function send(code) {
    code = (code || "").trim();
    if (code.length < 4) return reset();
    reset();
    box.value = code;
    if (status) status.textContent = code;
    if (box.form) { if (box.form.requestSubmit) box.form.requestSubmit(); else box.form.submit(); }
  }

  document.addEventListener("keydown", function (ev) {
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    var now = Date.now(), el = document.activeElement;
    var inBox = el === box;
    if (editable(el) && !inBox) return;          // somebody is typing in another field: not a scan
    if (ev.key === "Enter" || ev.key === "Tab") {
      if (buf.length >= MIN - 2 && fast()) { ev.preventDefault(); send(buf); }
      else reset();
      return;
    }
    if (ev.key.length !== 1) return;              // shift, arrows, function keys...
    if (times.length && now - times[times.length - 1] > GAP) reset();   // a pause: start counting again
    buf += ev.key; times.push(now);
    if (!inBox) { ev.preventDefault(); box.value = buf; }   // nothing focused: the scan lands in the scan box
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {             // scanner with no Enter key: the silence after the last digit ends the code
      if (buf.length >= MIN && fast()) send(buf); else reset();
    }, IDLE);
  }, true);

  // typing or pasting the barcode: once it has a complete barcode length (EAN-13, UPC-A, EAN-8...) and you stop, it is checked by itself
  var typedTimer = null;
  box.addEventListener("input", function (ev) {
    if (typedTimer) { clearTimeout(typedTimer); typedTimer = null; }
    var v = box.value.trim();
    if (!/^\d+$/.test(v)) return;
    var paste = ev.inputType === "insertFromPaste" || ev.inputType === "insertFromDrop";
    var wait = paste ? 0 : (v.length === 13 || v.length === 12 || v.length === 14) ? 300 : v.length === 8 ? 1200 : -1;
    if (wait < 0 || (paste && v.length < 6)) return;
    typedTimer = setTimeout(function () { if (box.value.trim() === v) send(v); }, wait);
  });

  // keep the scan box ready: focus it on load and whenever a click lands on empty page space
  box.focus();
  document.addEventListener("click", function (ev) { if (navigator.maxTouchPoints > 0) return; if (!editable(ev.target) && !ev.target.closest("a,button,label,select")) box.focus(); });
  if (status) status.setAttribute("data-ready", "1");
})();
