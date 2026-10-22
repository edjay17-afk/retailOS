/* Live camera scanner, like a QR-scanner app: a button with data-scan="<input id>" opens a full-screen camera view with a scan frame,
   reads the first barcode / QR code it sees, puts it in that input and submits its form.
   Uses the browser's own BarcodeDetector when it has one (Android Chrome), otherwise the bundled ZXing library (iPhone Safari, desktop).
   Browsers only allow the camera on https:// or localhost, and not inside in-app browsers (Facebook, Lark...). Open the https address in Chrome or Safari.
   The picture never leaves the phone: it is read inside the browser. */
(function () {
  var ZX = "/static/vendor/zxing-library.min.js";
  function loadZX(cb) {
    if (window.ZXing) return cb();
    var s = document.createElement("script"); s.src = ZX; s.onload = function () { cb(); };
    s.onerror = function () { cb("The scanner could not be loaded"); };
    document.head.appendChild(s);
  }
  function submitWith(input, code) {
    input.value = String(code).trim();
    if (input.hasAttribute("data-scan-fill-only")) {
      input.dispatchEvent(new Event("input", {bubbles:true}));
      var qty = input.form && input.form.elements.namedItem("qty");
      (qty || input).focus({preventScroll:true});
      return;
    }
    if (input.form) { if (input.form.requestSubmit) input.form.requestSubmit(); else input.form.submit(); }
  }
  function zxHints() {
    var Z = window.ZXing, h = new Map();
    h.set(Z.DecodeHintType.TRY_HARDER, true);
    h.set(Z.DecodeHintType.POSSIBLE_FORMATS, [Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.EAN_8, Z.BarcodeFormat.UPC_A, Z.BarcodeFormat.UPC_E,
      Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.ITF, Z.BarcodeFormat.QR_CODE]);
    return h;
  }
  function explain(e) {
    if (!window.isSecureContext) return "The camera only works on a secure (https) address. Open Retail OS using its https link, in Chrome or Safari.";
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return "This browser cannot use the camera here. Open the https link in Chrome or Safari (not inside another app).";
    if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) return "Camera permission was refused. Allow the camera for this site in the browser settings, then try again.";
    if (e && e.name === "NotFoundError") return "No camera was found on this device.";
    if (e && e.name === "NotReadableError") return "The camera is being used by another app. Close it and try again.";
    return "The camera could not be opened" + (e && e.message ? ": " + e.message : ".");
  }

  function open(input) {
    var box = document.createElement("dialog");
    box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Scan a barcode");
    box.style.cssText = "position:fixed;inset:0;width:100vw;height:100dvh;max-width:none;max-height:none;margin:0;padding:0;border:0;border-radius:0;background:#000;z-index:9999;display:flex;flex-direction:column;animation:none";
    box.innerHTML =
      '<div style="position:relative;flex:1;overflow:hidden">' +
        '<video playsinline muted autoplay style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>' +
        '<div data-frame style="position:absolute;left:50%;top:50%;width:min(78vw,420px);height:min(34vw,180px);transform:translate(-50%,-50%);border:3px solid #fff;border-radius:14px;box-shadow:0 0 0 100vmax rgba(0,0,0,.5)">' +
          '<div data-line style="position:absolute;left:8px;right:8px;top:50%;height:2px;background:#ff3b30;box-shadow:0 0 8px #ff3b30"></div></div>' +
      '</div>' +
      '<div style="padding:14px 16px calc(14px + env(safe-area-inset-bottom));background:#111;color:#fff;text-align:center">' +
        '<div data-msg style="font-size:15px;margin-bottom:10px">Point the camera at the barcode or QR code</div>' +
        '<div style="display:flex;gap:10px;justify-content:center"><button type="button" class="sec" data-torch hidden>Light</button><button type="button" class="sec" data-close>Cancel</button></div>' +
      '</div>';
    document.body.appendChild(box);
    box.showModal();
    var video = box.querySelector("video"), msg = box.querySelector("[data-msg]"), torchBtn = box.querySelector("[data-torch]");
    var stream = null, stopped = false, detector = null, canvas = document.createElement("canvas"), reader = null, torchOn = false;
    function stop() {
      stopped = true;
      if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
      box.close();
      box.remove();
    }
    function done(code) {
      if (stopped || !code) return;
      if (navigator.vibrate) try { navigator.vibrate(60); } catch (e) {}
      stop(); submitWith(input, code);
    }
    box.querySelector("[data-close]").onclick = stop;
    box.addEventListener("cancel", function(e){ e.preventDefault(); stop(); });
    var line = box.querySelector("[data-line]"), t0 = Date.now();
    (function sweep() { if (stopped) return; line.style.top = (50 + 42 * Math.sin((Date.now() - t0) / 450)) + "%"; requestAnimationFrame(sweep); })();

    function tick() {
      if (stopped) return;
      if (video.readyState < 2 || !video.videoWidth) return setTimeout(tick, 40);
      if (detector) {
        detector.detect(video).then(function (r) { if (r.length) done(r[0].rawValue); else setTimeout(tick, 30); }).catch(function () { setTimeout(tick, 120); });
      } else {
        // read only the middle band where the scan frame is: a third of the pixels, so each try is much faster
        var vw = video.videoWidth, vh = video.videoHeight, sx = Math.round(vw * 0.08), sw = vw - 2 * sx, sy = Math.round(vh * 0.25), sh = Math.round(vh * 0.5);
        var sc = Math.min(1, 900 / sw), w = Math.round(sw * sc), h = Math.round(sh * sc);
        canvas.width = w; canvas.height = h;
        canvas.getContext("2d", { willReadFrequently: true }).drawImage(video, sx, sy, sw, sh, 0, 0, w, h);
        try {
          var src = new window.ZXing.HTMLCanvasElementLuminanceSource(canvas);
          done(reader.decode(new window.ZXing.BinaryBitmap(new window.ZXing.HybridBinarizer(src))).getText());
        } catch (e) { setTimeout(tick, 0); }  // nothing readable in this frame yet: try the next one straight away
      }
    }

    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { msg.textContent = explain(); return; }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false }).then(function (st) {
      if (stopped) { st.getTracks().forEach(function (t) { t.stop(); }); return; }
      stream = st; video.srcObject = st;
      var p = video.play(); if (p && p.catch) p.catch(function () {});
      var track = st.getVideoTracks()[0], caps = track.getCapabilities ? track.getCapabilities() : {};
      try { if (caps.focusMode && caps.focusMode.indexOf("continuous") >= 0) track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }); } catch (e) {}
      if (caps.torch) {
        torchBtn.hidden = false;
        torchBtn.onclick = function () { torchOn = !torchOn; track.applyConstraints({ advanced: [{ torch: torchOn }] }).catch(function () {}); };
      }
      if ("BarcodeDetector" in window) {
        try {
          detector = new window.BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "itf", "qr_code"] });
        } catch (e) { detector = null; }
      }
      if (detector) return tick();
      loadZX(function (err) {
        if (err) { msg.textContent = err; return; }
        reader = new window.ZXing.MultiFormatReader(); reader.setHints(zxHints());
        tick();
      });
    }).catch(function (e) { msg.textContent = explain(e); });
  }

  // fetch the reader in the background as soon as the page is idle, so the first scan does not wait for the download
  if (document.querySelector("[data-scan]") && !("BarcodeDetector" in window)) {
    (window.requestIdleCallback || function (f) { setTimeout(f, 800); })(function () { loadZX(function () {}); });
  }
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-scan]");
    if (!b) return;
    ev.preventDefault();
    var input = document.getElementById(b.getAttribute("data-scan"));
    if (input) open(input);
  });
})();
