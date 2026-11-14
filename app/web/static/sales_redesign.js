(function () {
  var page = document.querySelector(".sales-page");
  if (!page) return;

  // 1. Location selector auto-submit (CSP-clean)
  var locSel = document.getElementById("sales-location");
  if (locSel && locSel.form) {
    locSel.addEventListener("change", function () { locSel.form.submit(); });
  }

  // 2. Trajectory chart state & renderer
  var dataEl = document.getElementById("sales-chart-data");
  var svg = document.getElementById("sales-main-chart");
  var tip = document.getElementById("sales-chart-tip");
  var chartTitle = document.getElementById("sales-chart-title");
  var cmpBtn = document.getElementById("sales-cmp-btn");
  var prevLg = document.getElementById("sales-prev-legend");
  var locInput = page.dataset.loc || "0";

  var payload = null;
  if (dataEl) {
    try { payload = JSON.parse(dataEl.textContent || "{}"); } catch (e) { payload = null; }
  }

  var state = { metric: "net", mode: "line", compare: true };
  if (payload && (!payload.net_prev || !payload.net_prev.length) && cmpBtn) {
    state.compare = false;
    cmpBtn.classList.remove("on");
    cmpBtn.hidden = true;
    if (prevLg) prevLg.hidden = true;
  }

  function fmtMoney(n) {
    return Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtInt(n) {
    return Math.round(Number(n || 0)).toLocaleString("en-US");
  }
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function renderChart() {
    if (!svg || !payload || !payload.labels || !payload.labels.length) return;
    var vals = payload[state.metric] || [];
    var prevs = state.compare ? (payload[state.metric + "_prev"] || []) : [];
    var hasPrev = prevs.length === vals.length && prevs.some(function (x) { return x > 0; });
    if (prevLg) prevLg.hidden = !(state.compare && hasPrev);

    if (chartTitle) {
      var key = payload.hourly ? ("hourly_" + state.metric) : state.metric;
      if (chartTitle.dataset[key]) chartTitle.textContent = chartTitle.dataset[key];
    }

    var W = 760, H = 245, padL = 56, padR = 14, padT = 16, padB = 30;
    var iw = W - padL - padR, ih = H - padT - padB;
    var allV = vals.concat(hasPrev ? prevs : []);
    var maxV = Math.max.apply(null, allV.concat([1])) * 1.12;
    if (maxV <= 0) maxV = 1;

    var parts = [];
    for (var g = 0; g <= 4; g++) {
      var gy = padT + (ih * g / 4);
      var gv = maxV * (1 - g / 4);
      var lbl = state.metric === "tx" ? fmtInt(gv) : (gv >= 1000 ? Math.round(gv / 1000) + "k" : Math.round(gv));
      parts.push('<line x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + gy.toFixed(1) + '" stroke="var(--line)" stroke-width="1"/>');
      parts.push('<text x="' + (padL - 8) + '" y="' + (gy + 4).toFixed(1) + '" text-anchor="end" font-size="11" fill="var(--mute)">' + esc(lbl) + "</text>");
    }

    var n = vals.length;
    var slot = iw / n;
    var bw = Math.min(30, slot * 0.56);
    var skipEven = n > 8;

    if (state.mode === "bars") {
      for (var i = 0; i < n; i++) {
        var cx = padL + slot * (i + 0.5);
        var bh = Math.max(0, (vals[i] / maxV) * ih);
        var by = padT + ih - bh;
        parts.push('<rect x="' + (cx - bw / 2).toFixed(1) + '" y="' + by.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(2, bh).toFixed(1) + '" rx="5" fill="var(--primary)" opacity="0.9"/>');
        var altCls = skipEven && (i % 2 === 1) ? " alt-hide" : "";
        parts.push('<text class="sd-chart-x' + altCls + '" x="' + cx.toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="11" fill="var(--mute)">' + esc(payload.labels[i]) + "</text>");
      }
      if (hasPrev) {
        var pd = [];
        for (var j = 0; j < n; j++) {
          var pcx = padL + slot * (j + 0.5);
          var py = padT + ih - (prevs[j] / maxV) * ih;
          pd.push((j === 0 ? "M" : "L") + pcx.toFixed(1) + "," + py.toFixed(1));
        }
        parts.push('<path d="' + pd.join(" ") + '" fill="none" stroke="var(--mute2)" stroke-width="2" stroke-dasharray="5 4"/>');
      }
    } else {
      var coords = [];
      for (var k = 0; k < n; k++) {
        coords.push([padL + slot * (k + 0.5), padT + ih - (vals[k] / maxV) * ih]);
      }
      var areaD = "M" + coords[0][0].toFixed(1) + "," + (padT + ih) + " " +
        coords.map(function (c) { return "L" + c[0].toFixed(1) + "," + c[1].toFixed(1); }).join(" ") +
        " L" + coords[coords.length - 1][0].toFixed(1) + "," + (padT + ih) + " Z";
      var lineD = coords.map(function (c, idx) { return (idx === 0 ? "M" : "L") + c[0].toFixed(1) + "," + c[1].toFixed(1); }).join(" ");
      parts.push('<path d="' + areaD + '" fill="var(--primary)" opacity="0.14"/>');
      if (hasPrev) {
        var prevD = [];
        for (var m = 0; m < n; m++) {
          var mcx = padL + slot * (m + 0.5);
          var mpy = padT + ih - (prevs[m] / maxV) * ih;
          prevD.push((m === 0 ? "M" : "L") + mcx.toFixed(1) + "," + mpy.toFixed(1));
        }
        parts.push('<path d="' + prevD.join(" ") + '" fill="none" stroke="var(--mute2)" stroke-width="2" stroke-dasharray="5 4"/>');
      }
      parts.push('<path d="' + lineD + '" fill="none" stroke="var(--primary)" stroke-width="2.5"/>');
      for (var c = 0; c < n; c++) {
        parts.push('<circle cx="' + coords[c][0].toFixed(1) + '" cy="' + coords[c][1].toFixed(1) + '" r="4.2" fill="var(--primary)" stroke="var(--surface)" stroke-width="2"/>');
        var lCls = skipEven && (c % 2 === 1) ? " alt-hide" : "";
        parts.push('<text class="sd-chart-x' + lCls + '" x="' + coords[c][0].toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="11" fill="var(--mute)">' + esc(payload.labels[c]) + "</text>");
      }
    }

    for (var h = 0; h < n; h++) {
      var hx = padL + slot * h;
      parts.push('<rect class="sd-chart-hit" data-idx="' + h + '" x="' + hx.toFixed(1) + '" y="' + padT + '" width="' + slot.toFixed(1) + '" height="' + ih + '" fill="transparent" style="cursor:pointer"/>');
    }

    svg.innerHTML = parts.join("");

    if (tip) {
      svg.querySelectorAll(".sd-chart-hit").forEach(function (hit) {
        hit.addEventListener("mouseenter", function () {
          var idx = parseInt(hit.dataset.idx, 10);
          var v = vals[idx] || 0;
          var p = hasPrev ? (prevs[idx] || 0) : null;
          var vStr = state.metric === "tx" ? fmtInt(v) : fmtMoney(v);
          var html = "<b>" + esc(payload.labels[idx]) + '</b><div class="sales-tt-row"><span>' + esc(page.dataset.lblThis || "This period") + "</span><strong>" + esc(vStr) + "</strong></div>";
          if (p !== null && p > 0) {
            var diff = ((v - p) / p * 100);
            var pStr = state.metric === "tx" ? fmtInt(p) : fmtMoney(p);
            html += '<div class="sales-tt-row"><span>' + esc(page.dataset.lblPrev || "Prev") + "</span><span>" + esc(pStr) + " (" + (diff >= 0 ? "+" : "") + diff.toFixed(1) + "%)</span></div>";
          }
          tip.innerHTML = html;
          var box = svg.getBoundingClientRect();
          var xFrac = (idx + 0.5) / n;
          tip.style.left = Math.round(box.width * (0.08 + xFrac * 0.88)) + "px";
          tip.style.top = "26px";
          tip.classList.add("show");
        });
        hit.addEventListener("mouseleave", function () { tip.classList.remove("show"); });
        hit.addEventListener("click", function () {
          var idx = parseInt(hit.dataset.idx, 10);
          if (payload.dates && payload.dates[idx]) openDayDrawer(payload.dates[idx]);
        });
      });
    }
  }

  document.querySelectorAll("[data-sales-metric]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("[data-sales-metric]").forEach(function (b) { b.classList.remove("on"); });
      btn.classList.add("on");
      state.metric = btn.dataset.salesMetric;
      renderChart();
    });
  });

  document.querySelectorAll("[data-sales-mode]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("[data-sales-mode]").forEach(function (b) { b.classList.remove("on"); });
      btn.classList.add("on");
      state.mode = btn.dataset.salesMode;
      renderChart();
    });
  });

  if (cmpBtn) {
    cmpBtn.addEventListener("click", function () {
      state.compare = !state.compare;
      cmpBtn.classList.toggle("on", state.compare);
      renderChart();
    });
  }

  renderChart();

  // 3. Top products Revenue / Units toggle
  document.querySelectorAll("[data-prod-view]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var mode = btn.dataset.prodView;
      document.querySelectorAll("[data-prod-view]").forEach(function (b) { b.classList.toggle("on", b === btn); });
      document.querySelectorAll("[data-prod-pane]").forEach(function (pane) {
        pane.hidden = pane.dataset.prodPane !== mode;
      });
    });
  });

  // 4. Client-side sorting for By Day table
  var tbl = document.getElementById("sales-days-table");
  if (tbl && tbl.tBodies.length) {
    var tbody = tbl.tBodies[0];
    var sortState = { col: "date", dir: "desc" };
    tbl.querySelectorAll("th.sort").forEach(function (th) {
      th.addEventListener("click", function () {
        var col = th.dataset.sk;
        sortState.dir = sortState.col === col && sortState.dir === "desc" ? "asc" : "desc";
        sortState.col = col;
        var rows = Array.prototype.slice.call(tbody.rows);
        rows.sort(function (a, b) {
          var av = a.dataset[col] || "";
          var bv = b.dataset[col] || "";
          if (col !== "date") {
            av = parseFloat(av) || 0;
            bv = parseFloat(bv) || 0;
          }
          if (av === bv) return 0;
          var cmp = av > bv ? 1 : -1;
          return sortState.dir === "asc" ? cmp : -cmp;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  }

  // 5. Slide-over Day Detail Drawer (/sales/day-panel)
  var scrim = document.getElementById("sales-scrim");
  var drawer = document.getElementById("sales-drawer");
  var drTitle = document.getElementById("sales-dr-title");
  var drSub = document.getElementById("sales-dr-sub");
  var drBody = document.getElementById("sales-dr-body");
  var drClose = document.getElementById("sales-dr-close");

  function closeDrawer() {
    if (scrim) scrim.classList.remove("open");
    if (drawer) {
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
    }
    if (tbl) {
      tbl.querySelectorAll("tbody tr").forEach(function (tr) { tr.classList.remove("selected"); });
    }
  }

  function openDayDrawer(dayIso) {
    if (!drawer || !drBody || !dayIso) return;
    if (tbl) {
      tbl.querySelectorAll("tbody tr").forEach(function (tr) {
        tr.classList.toggle("selected", tr.dataset.date === dayIso);
      });
    }
    if (scrim) scrim.classList.add("open");
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    if (drTitle) drTitle.textContent = dayIso;
    if (drSub) drSub.textContent = page.dataset.lblLoading || "Loading...";
    drBody.innerHTML = '<div class="mute" style="padding:20px 0">' + esc(page.dataset.lblLoading || "Loading...") + "</div>";

    fetch("/sales/day-panel?day=" + encodeURIComponent(dayIso) + "&loc=" + encodeURIComponent(locInput), { credentials: "same-origin" })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.text();
      })
      .then(function (html) {
        drBody.innerHTML = html;
        var meta = drBody.querySelector(".sd-dr-head-meta");
        if (meta) {
          if (drTitle && meta.dataset.title) drTitle.textContent = meta.dataset.title;
          if (drSub && meta.dataset.sub) drSub.textContent = meta.dataset.sub;
        }
      })
      .catch(function () {
        drBody.innerHTML = '<div class="mute" style="padding:20px 0">Could not load day summary.</div>';
      });
  }

  if (scrim) scrim.addEventListener("click", closeDrawer);
  if (drClose) drClose.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && drawer && drawer.classList.contains("open")) closeDrawer();
  });

  // Capture-phase row & signal handler so row clicks open the drawer instead of app.js navigating
  document.addEventListener("click", function (e) {
    var sig = e.target.closest("[data-day-open]");
    if (sig) {
      e.preventDefault();
      e.stopPropagation();
      openDayDrawer(sig.dataset.dayOpen);
      return;
    }
    var tr = e.target.closest("#sales-days-table tbody tr[data-date]");
    if (tr) {
      e.preventDefault();
      e.stopPropagation();
      openDayDrawer(tr.dataset.date);
    }
  }, true);
})();
