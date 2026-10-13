/* Chart tooltips and the floating assistant chat. Loaded from base.html (script-src 'self' is allowed by the CSP). */
(function () {
  var messageNode = document.getElementById('ui-messages');
  var messages = messageNode ? JSON.parse(messageNode.textContent) : {};
  function t(key, values) {
    var text = Object.prototype.hasOwnProperty.call(messages, key) ? messages[key] : key;
    return text.replace(/\{(\w+)\}/g, function (match, name) {
      return values && Object.prototype.hasOwnProperty.call(values, name) ? values[name] : match;
    });
  }
  // Dark mode: <html data-theme> was already set before paint (see base.html); here we only wire the topbar toggle.
  var themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    var paintTheme = function () {
      var dark = document.documentElement.dataset.theme === 'dark';
      themeBtn.setAttribute('aria-pressed', dark ? 'true' : 'false');
      var use = themeBtn.querySelector('use');
      if (use) use.setAttribute('href', dark ? '#i-sun' : '#i-moon');
      themeBtn.title = dark ? t('Light mode') : t('Dark mode');
    };
    paintTheme();
    themeBtn.addEventListener('click', function () {
      var dark = document.documentElement.dataset.theme === 'dark';
      var next = dark ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('ros-theme', next); } catch (e) { /* private mode: theme just resets next visit */ }
      paintTheme();
    });
  }
  // Flash messages (saved, deleted, errors) fade away on their own, and the ?ok= / ?err= part is dropped from the address so a refresh does not show them again.
  var flashes = document.querySelectorAll('.flash');
  if (flashes.length) {
    try {
      var url = new URL(window.location.href);
      if (url.searchParams.has('ok') || url.searchParams.has('err')) {
        url.searchParams.delete('ok'); url.searchParams.delete('err');
        window.history.replaceState(null, '', url.pathname + url.search + url.hash);
      }
    } catch (e) { /* the message still fades */ }
    flashes.forEach(function (box) {
      var close = function () { box.classList.add('flash-out'); setTimeout(function () { box.remove(); }, 450); };
      box.addEventListener('click', close); // a tap also dismisses it
      var wait = box.classList.contains('err') ? 9000 : 4000; // errors stay longer: they may list rows to fix
      setTimeout(close, wait);
    });
  }
  // Modal dialogs: <button data-modal="id"> opens <dialog id="id">; [data-modal-close], a click on the dim backdrop and Esc close it.
  document.addEventListener('click', function (e) {
    var opener = e.target.closest && e.target.closest('[data-modal]');
    if (opener) {
      var dlg = document.getElementById(opener.getAttribute('data-modal'));
      if (dlg && typeof dlg.showModal === 'function') { dlg.showModal(); var f = dlg.querySelector('input:not([type=hidden])'); if (f) f.focus(); }
      return;
    }
    var closer = e.target.closest && e.target.closest('[data-modal-close]');
    if (closer) { var d = closer.closest('dialog'); if (d) d.close(); return; }
    if (e.target.tagName === 'DIALOG' && e.target.classList.contains('modal')) e.target.close(); // the backdrop is the dialog element itself
  });
  // Phone layout for lists: every table row becomes a card, so nothing needs sideways scrolling. Each cell is tagged with its column heading
  // (shown above the value on small screens), the most important cell becomes the card title and the buttons sit at the bottom.
  (function () {
    if (/^\/(settings|audit|sync|assistant)/.test(window.location.pathname)) return;
    document.querySelectorAll('.tw > table').forEach(function (tb) {
      if (tb.classList.contains('rt') || tb.classList.contains('document-edit-table')) return;
      var rows = [].slice.call(tb.querySelectorAll('tr'));
      var head = rows.filter(function (r) { return r.querySelector('th') && !r.querySelector('td'); })[0];
      if (!head) return;
      var labels = [].map.call(head.children, function (th) { return th.textContent.replace(/\s+/g, ' ').trim(); });
      var done = false;
      rows.forEach(function (r) {
        if (r === head) return;
        var cells = [].slice.call(r.children).filter(function (td) { return td.style.display !== 'none'; }); // a hidden helper cell (it holds a form) is not a column
        if (cells.length !== labels.length) return; // an empty-state row or a merged row stays as it is
        cells.forEach(function (td, i) {
          td.setAttribute('data-label', labels[i]);
          if (/^(#|no\.?)$/i.test(labels[i])) td.classList.add('rc-hide'); // a bare row number adds nothing on a card
          else if (!td.textContent.trim() && !td.querySelector('img,svg,input,button,a,video,.bar')) td.classList.add('rc-empty'); // no value, so no label either
        });
        var titleIdx = -1; // the first column that has a value is the card's title
        cells.forEach(function (td, i) { if (titleIdx < 0 && labels[i] && !/^(#|no\.?)$/i.test(labels[i]) && td.textContent.trim()) titleIdx = i; });
        if (titleIdx >= 0) cells[titleIdx].classList.add('rc-title');
        var last = cells[cells.length - 1];
        if (last && last !== cells[titleIdx] && last.querySelector('.btn,button,form,details')) last.classList.add('rc-act');
        done = true;
      });
      if (done) tb.classList.add('rcards');
    });
  })();
  // ---- Laws of UX helpers ----
  // Doherty threshold: a press is acknowledged at once (spinner on the button, a thin loading bar at the top) and a form cannot be sent twice.
  function wantsBusy(f) {
    if (f.hasAttribute('data-busy')) return true;
    if (f.hasAttribute('data-nobusy') || f.hasAttribute('data-explain') || f.target || (f.id && /^chat/.test(f.id))) return false;
    if (document.querySelector('script[src*="documents.js"]')) return false; // the document editor saves in place and manages its own buttons
    return (f.method || '').toLowerCase() === 'post';
  }
  function releaseBusy() {
    document.body.classList.remove('nav-loading');
    document.querySelectorAll('form.is-busy').forEach(function (f) {
      f.classList.remove('is-busy'); f.removeAttribute('aria-busy');
      f.querySelectorAll('button').forEach(function (b) { b.disabled = false; b.classList.remove('btn-busy'); });
    });
  }
  window.addEventListener('pageshow', function (e) { if (e.persisted) releaseBusy(); });
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target || a.hasAttribute('download') || a.hasAttribute('data-modal') || /^(#|mailto:|tel:|javascript:)/.test(a.getAttribute('href'))) return;
    if (a.origin !== window.location.origin || /\.(csv|pdf|xlsx?)(\?|$)/i.test(a.pathname) || /export=|\/export\b/.test(a.search + a.pathname)) return; // downloads do not leave the page
    document.body.classList.add('nav-loading');
  });
  // Postel's law: be forgiving in what people type. Spaces around text are dropped before a form is checked or sent.
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!(f instanceof HTMLFormElement)) return;
    f.querySelectorAll('input:not([type]),input[type=text],input[type=search],input[type=email],input[type=tel],textarea').forEach(function (i) {
      if (i.value !== i.value.trim()) i.value = i.value.trim();
    });
  }, true);
  // Jakob's law: the familiar "/" shortcut jumps to search.
  document.addEventListener('keydown', function (e) {
    if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
    var tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) return;
    var box = document.querySelector('[data-search]');
    if (box) { e.preventDefault(); box.focus(); box.select(); }
  });
  // Zeigarnik effect / error prevention: a half-filled checklist or count is not lost by a stray click on another page.
  (function () {
    var dirty = false;
    document.querySelectorAll('form[data-guard]').forEach(function (f) {
      f.addEventListener('input', function () { dirty = true; });
      f.addEventListener('change', function () { dirty = true; });
      f.addEventListener('submit', function () { dirty = false; });
    });
    window.addEventListener('beforeunload', function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  })();
  // Shared workspace navigation. Layout preference never stores business data.
  var railToggle = document.getElementById('rail-toggle');
  function setRail(expanded) {
    document.body.classList.toggle('rail-expanded', expanded);
    if (railToggle) {
      railToggle.setAttribute('aria-expanded', String(expanded));
      railToggle.setAttribute('aria-label', t(expanded ? 'Collapse navigation' : 'Expand navigation'));
      railToggle.title = t(expanded ? 'Collapse navigation' : 'Expand navigation');
    }
  }
  try { setRail(localStorage.getItem('retailos.navigation') !== 'compact'); } catch (e) { setRail(true); }
  if (railToggle) railToggle.addEventListener('click', function () {
    var expanded = !document.body.classList.contains('rail-expanded');
    setRail(expanded);
    try { localStorage.setItem('retailos.navigation', expanded ? 'expanded' : 'compact'); } catch (e) { /* optional preference */ }
  });
  var mobileMenu = document.getElementById('mobile-menu'), sidebar = document.getElementById('sidebar');
  var mainWorkspace = document.querySelector('.main'), narrowNavigation = window.matchMedia('(max-width:1000px)');
  function setMobile(open) {
    open = open && narrowNavigation.matches;
    document.body.classList.toggle('nav-open', open);
    if (sidebar) sidebar.inert = narrowNavigation.matches && !open;
    if (mainWorkspace) mainWorkspace.inert = open;
    if (mobileMenu) {
      mobileMenu.setAttribute('aria-expanded', String(open));
      mobileMenu.setAttribute('aria-label', t(open ? 'Close navigation' : 'Open navigation'));
    }
    if (open && sidebar) { var first = sidebar.querySelector('a'); if (first) first.focus(); }
  }
  setMobile(false);
  narrowNavigation.addEventListener('change', function () { setMobile(false); });
  if (mobileMenu) mobileMenu.addEventListener('click', function () { setMobile(!document.body.classList.contains('nav-open')); });
  var drawerClose = document.getElementById('navigation-close');
  if (drawerClose) drawerClose.addEventListener('click', function () { setMobile(false); if (mobileMenu) mobileMenu.focus(); });
  var scrim = document.querySelector('.scrim');
  if (scrim) scrim.addEventListener('click', function () { setMobile(false); if (mobileMenu) mobileMenu.focus(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && document.body.classList.contains('nav-open')) { setMobile(false); if (mobileMenu) mobileMenu.focus(); }
    if (e.key === 'Tab' && document.body.classList.contains('nav-open') && sidebar) {
      var links = sidebar.querySelectorAll('a[href],button:not([disabled])');
      var first = links[0], last = links[links.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  document.querySelectorAll('[data-print]').forEach(function (button) { button.addEventListener('click', function () { window.print(); }); });
  // Shared form feedback. The server remains authoritative; client constraints
  // are presented inline and associated with the field rather than in OS bubbles.
  var errorId = 0;
  function clearFieldError(field) {
    var id = field.getAttribute('data-error-id');
    if (!id) return;
    var error = document.getElementById(id);
    if (error) error.remove();
    var described = (field.getAttribute('aria-describedby') || '').split(/\s+/).filter(function (part) { return part && part !== id; });
    if (described.length) field.setAttribute('aria-describedby', described.join(' '));
    else field.removeAttribute('aria-describedby');
    field.removeAttribute('aria-invalid'); field.removeAttribute('data-error-id');
  }
  function fieldMessage(field) {
    var validity = field.validity;
    if (validity.valueMissing) return t('Please complete this field.');
    if (validity.rangeUnderflow) return t('Enter a value of at least {value}.', { value: field.min });
    if (validity.rangeOverflow) return t('Enter a value no greater than {value}.', { value: field.max });
    if (validity.tooShort) return t('Enter at least {value} characters.', { value: field.minLength });
    if (validity.typeMismatch && field.type === 'email') return t('Enter a valid email address.');
    if (validity.badInput || validity.stepMismatch) return t('Enter a valid number using the required increment.');
    return t('Check this value and try again.');
  }
  function validateForm(form) {
    var first = null;
    Array.prototype.forEach.call(form.elements, function (field) {
      if (!field.willValidate) return;
      clearFieldError(field);
      if (field.validity.valid) return;
      var error = document.createElement('span'), id = 'field-error-' + (++errorId);
      error.id = id; error.className = 'field-error'; error.textContent = fieldMessage(field);
      field.insertAdjacentElement('afterend', error);
      field.setAttribute('data-error-id', id); field.setAttribute('aria-invalid', 'true');
      field.setAttribute('aria-describedby', ((field.getAttribute('aria-describedby') || '') + ' ' + id).trim());
      if (!first) first = field;
    });
    if (first) { first.focus(); first.scrollIntoView({ block: 'nearest' }); }
    return !first;
  }
  document.addEventListener('input', function (event) { if (event.target.validity && event.target.validity.valid) clearFieldError(event.target); });
  document.addEventListener('change', function (event) { if (event.target.validity && event.target.validity.valid) clearFieldError(event.target); });

  // Confirmation is app-owned: Cancel receives focus, Escape cancels, the
  // native dialog isolates the background, and closing restores the trigger.
  var confirmDialog = document.getElementById('confirm-dialog'), confirmTrigger = null;
  var approvedForms = new WeakSet();
  function trapDialogFocus(dialog) {
    dialog.addEventListener('keydown', function (event) {
      if (event.key !== 'Tab') return;
      var controls = Array.prototype.filter.call(dialog.querySelectorAll('button:not([disabled]),a[href],input:not([type=hidden]):not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex="0"]'), function (control) { return control.getClientRects().length; });
      if (!controls.length) { event.preventDefault(); dialog.focus(); return; }
      var first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
  }
  if (confirmDialog) {
    trapDialogFocus(confirmDialog);
    var confirmCancel = document.getElementById('confirm-cancel'), confirmAccept = document.getElementById('confirm-accept');
    confirmCancel.addEventListener('click', function () { confirmDialog.close(); });
    confirmDialog.addEventListener('close', function () {
      document.body.classList.remove('confirm-open');
      if (confirmTrigger && confirmTrigger.isConnected) confirmTrigger.focus();
      confirmTrigger = null;
    });
    confirmAccept.addEventListener('click', function () {
      var trigger = confirmTrigger, form = trigger && trigger.form;
      confirmDialog.close();
      if (form) { approvedForms.add(form); form.requestSubmit(trigger); }
    });
  }
  document.addEventListener('submit', function (event) {
    var form = event.target, trigger = event.submitter;
    if (!(form instanceof HTMLFormElement)) return;
    if (!(trigger && trigger.formNoValidate) && !validateForm(form)) {
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    if (trigger && trigger.hasAttribute('data-confirm') && !approvedForms.has(form)) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (!confirmDialog) return; // never send a destructive action without confirmation
      confirmTrigger = trigger;
      document.getElementById('confirm-title').textContent = trigger.textContent.trim();
      document.getElementById('confirm-description').textContent = trigger.getAttribute('data-confirm');
      confirmAccept.textContent = trigger.textContent.trim();
      document.body.classList.add('confirm-open'); confirmDialog.showModal(); confirmCancel.focus();
      return;
    }
    approvedForms.delete(form);
    if (wantsBusy(form)) {
      if (form.classList.contains('is-busy')) { event.preventDefault(); event.stopImmediatePropagation(); return; }
      form.classList.add('is-busy'); form.setAttribute('aria-busy', 'true');
      if (trigger) trigger.classList.add('btn-busy');
      if (!form.hasAttribute('data-explain')) document.body.classList.add('nav-loading');
      requestAnimationFrame(function () { form.querySelectorAll('button').forEach(function (button) { button.disabled = true; }); });
      setTimeout(releaseBusy, 25000); // a response that is a file download never reloads the page: let the buttons work again
    }
  }, true);

  function enhanceFields(scope) {
    scope.querySelectorAll('input:not([type=hidden]),select,textarea').forEach(function (field) {
      if (field.labels && field.labels.length) return;
      var label = field.previousElementSibling;
      if (label && label.tagName === 'LABEL') {
        if (!field.id) field.id = 'field-' + (++errorId);
        label.htmlFor = field.id;
      } else if (!field.hasAttribute('aria-label') && field.placeholder) field.setAttribute('aria-label', field.placeholder);
    });
    scope.querySelectorAll('textarea').forEach(function (field) {
      function grow() { field.style.height = 'auto'; field.style.height = Math.min(Math.max(field.scrollHeight, 104), 360) + 'px'; }
      field.addEventListener('input', grow); grow();
    });
    scope.querySelectorAll('input[name="q"]').forEach(function (field) {
      if (field.parentElement.classList.contains('search-field')) return;
      var wrapper = document.createElement('span'), clear = document.createElement('button');
      wrapper.className = 'search-field'; field.parentNode.insertBefore(wrapper, field); wrapper.appendChild(field);
      clear.type = 'button'; clear.className = 'search-clear'; clear.textContent = '×'; clear.setAttribute('aria-label', t('Clear search'));
      wrapper.appendChild(clear);
      function update() { clear.hidden = !field.value; }
      clear.addEventListener('click', function () { field.value = ''; field.dispatchEvent(new Event('input', { bubbles: true })); field.focus(); });
      field.addEventListener('input', update); update();
    });
  }
  enhanceFields(document);

  // Animate only actual chart geometry. Labels and values stay exact, and no
  // polling/"live" badge is implied for StoreHub's imported data.
  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  function animateChart(chart) {
    if (reducedMotion.matches || !chart.animate) return;
    chart.querySelectorAll('.block-bar,.bar .b,.hb-t i').forEach(function (bar, index) {
      bar.animate([{ transform: bar.matches('.hb-t i') ? 'scaleX(0)' : 'scaleY(0)' }, { transform: 'none' }],
        { duration: 560, delay: Math.min(index * 22, 240), easing: 'cubic-bezier(.22,1,.36,1)', fill: 'backwards' });
    });
    chart.querySelectorAll('path[fill="none"]').forEach(function (path) {
      var length = path.getTotalLength();
      if (!length) return;
      path.animate([{ strokeDasharray: length + ' ' + length, strokeDashoffset: length }, { strokeDasharray: length + ' ' + length, strokeDashoffset: 0 }],
        { duration: 850, easing: 'cubic-bezier(.22,1,.36,1)', fill: 'backwards' });
    });
    chart.querySelectorAll('circle[pointer-events="none"][opacity]').forEach(function (point) {
      point.animate([{ opacity: .14 }, { opacity: .3 }, { opacity: .14 }], { duration: 1400, iterations: 2 });
    });
  }
  document.querySelectorAll('svg.chart [data-tip],svg.spark [data-tip],.donut .slice').forEach(function (point) {
    point.setAttribute('tabindex', '0'); point.setAttribute('aria-label', point.getAttribute('data-tip'));
  });
  if ('IntersectionObserver' in window) {
    var chartObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) { if (entry.isIntersecting) { animateChart(entry.target); chartObserver.unobserve(entry.target); } });
    }, { threshold: .15 });
    document.querySelectorAll('.block-chart,svg.chart,svg.spark,.hbars').forEach(function (chart) { chartObserver.observe(chart); });
  }
  reducedMotion.addEventListener('change', function (event) { if (event.matches) document.getAnimations().forEach(function (animation) { animation.cancel(); }); });
  // ---- tooltip: any element with data-tip shows its text next to the pointer (mouse) or on tap (touch)
  var tip = document.getElementById('tip'), cur = null;
  function place(x, y) {
    var w = tip.offsetWidth, h = tip.offsetHeight, L = x + 14, T = y - h - 12;
    if (L + w > window.innerWidth - 6) L = x - w - 14;
    if (L < 6) L = 6;
    if (T < 6) T = y + 18;
    tip.style.left = L + 'px'; tip.style.top = T + 'px';
  }
  function show(el, x, y) {
    var t = el.getAttribute('data-tip');
    if (!t) return;
    tip.textContent = t; tip.hidden = false; place(x, y);
  }
  if (tip) {
    document.addEventListener('focusin', function (e) {
      var el = e.target.closest && e.target.closest('[data-tip]');
      if (el) { var rect = el.getBoundingClientRect(); show(el, rect.left + rect.width / 2, rect.top); }
    });
    document.addEventListener('focusout', function () { tip.hidden = true; cur = null; });
    document.addEventListener('mousemove', function (e) {
      var el = e.target.closest && e.target.closest('[data-tip]');
      if (el) { cur = el; show(el, e.clientX, e.clientY); } else if (cur) { cur = null; tip.hidden = true; }
    });
    document.addEventListener('mouseleave', function () { tip.hidden = true; cur = null; });
    document.addEventListener('touchstart', function (e) {
      var t = e.touches[0], el = e.target.closest && e.target.closest('[data-tip]');
      if (el) show(el, t.clientX, t.clientY); else tip.hidden = true;
    }, { passive: true });
    window.addEventListener('scroll', function () { tip.hidden = true; }, { passive: true });
  }

  // ---- sortable tables: a th with data-sort sorts its tbody rows on click; numeric-aware, empty cells sort smallest
  function sortNum(s) {
    if (!/^[-+]?₱?[\d,]*\.?\d+%?$/.test(s)) return null;
    var n = parseFloat(s.replace(/[₱,%]/g, '').replace(/,/g, ''));
    return isNaN(n) ? null : n;
  }
  document.querySelectorAll('th[data-sort]').forEach(function (th) {
    th.setAttribute('tabindex', '0'); th.setAttribute('aria-sort', 'none');
    th.addEventListener('keydown', function (event) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); th.click(); } });
    th.addEventListener('click', function () {
      var table = th.closest('table'), body = table.tBodies[0];
      if (!body) return;
      var heads = th.parentNode.children, col = Array.prototype.indexOf.call(heads, th), ncols = heads.length;
      var rows = Array.prototype.slice.call(body.rows).filter(function (r) {
        return !r.querySelector('th') && !(r.cells.length === 1 && +r.cells[0].getAttribute('colspan') === ncols);
      });
      var dir = th.classList.contains('asc') ? -1 : 1;
      th.parentNode.querySelectorAll('th').forEach(function (h) { h.classList.remove('asc', 'desc'); if (h.hasAttribute('data-sort')) h.setAttribute('aria-sort', 'none'); });
      th.classList.add(dir === 1 ? 'asc' : 'desc');
      th.setAttribute('aria-sort', dir === 1 ? 'ascending' : 'descending');
      rows.sort(function (a, b) {
        var ci = function (r) { return r.cells[col + (r.cells.length - ncols)]; }; // rows may carry a hidden leading cell
        var av = (ci(a) ? ci(a).textContent : '').trim(), bv = (ci(b) ? ci(b).textContent : '').trim();
        if (av === '' && bv === '') return 0;
        if (av === '') return -dir; // empty always sorts smallest
        if (bv === '') return dir;
        var an = sortNum(av), bn = sortNum(bv);
        if (an !== null && bn !== null) return (an - bn) * dir;
        return av.localeCompare(bv) * dir;
      });
      rows.forEach(function (r) { body.appendChild(r); });
    });
  });

  // ---- clickable rows: tr[data-href] navigates unless the click was on an interactive element
  document.addEventListener('click', function (e) {
    var tr = e.target.closest && e.target.closest('tr[data-href]');
    if (!tr || e.target.closest('a,button,input,select,textarea,label,summary,details')) return;
    window.location.href = tr.getAttribute('data-href');
  });

  // ---- sales chart: Bars/Line toggle
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-chart]');
    if (!b) return;
    var card = b.closest('.card');
    card.querySelectorAll('[data-chart-pane]').forEach(function (p) { p.hidden = p.id !== b.getAttribute('data-chart'); });
    card.querySelectorAll('[data-chart]').forEach(function (c) { c.classList.toggle('on', c === b); });
    var pane = document.getElementById(b.getAttribute('data-chart'));
    if (pane) animateChart(pane);
  });

  // ---- inspection form: answered-counter, mark-all-pass, fail emphasis, upload tiles
  var iform = document.querySelector('form[data-total]');
  if (iform) {
    var prog = document.getElementById('chk-prog');
    var countAnswered = function () {
      var done = 0, groups = {};
      iform.querySelectorAll('input[name^="p_"]').forEach(function (r) { groups[r.name] = groups[r.name] || r.checked; });
      for (var g in groups) if (groups[g]) done++;
      iform.querySelectorAll('input[name^="v_"]').forEach(function (i) { if (i.value.trim() !== '') done++; });
      iform.querySelectorAll('.upl input[type=file][required]').forEach(function (i) { if (i.files && i.files.length) done++; });
      var totalItems = parseInt(iform.getAttribute('data-total'), 10) || 1, photoOnly = iform.hasAttribute('data-photo-only');
      if (photoOnly) done = iform.querySelectorAll('.upl.has-file').length;
      if (prog) {
        prog.textContent = t(photoOnly ? '{done} of {total} items photographed' : '{done} of {total} answered', { done: done, total: totalItems });
        prog.style.setProperty('--p', Math.min(100, Math.round(done / totalItems * 100)) + '%');
      }
    };
    var fmtSize = function (b) { return b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB'; };

    // upload tiles: show a preview row with a remove button once a file is picked
    iform.querySelectorAll('.upl').forEach(function (upl) {
      var input = upl.querySelector('input[type=file]'), prev = upl.querySelector('.upl-prev'), face = upl.querySelector('.upl-face b');
      if (!input || !prev) return;
      var label = face ? face.textContent : '';
      var selected = [], urls = [], multi = upl.hasAttribute('data-photos');
      var camera = upl.querySelector('[data-camera-input]'), cameraButton = upl.querySelector('[data-take-photo]');
      function syncFiles() {
        var transfer = new DataTransfer();
        selected.forEach(function (file) { transfer.items.add(file); });
        input.files = transfer.files;
      }
      function renderFiles() {
        urls.forEach(function (url) { URL.revokeObjectURL(url); }); urls = [];
        prev.replaceChildren();
        upl.classList.toggle('has-file', selected.length > 0);
        if (face && !multi) face.textContent = selected.length ? t('Tap to replace') : label;
        prev.hidden = selected.length === 0;
        var counter = upl.querySelector('.photo-count');
        if (counter) counter.textContent = selected.length ? t('{count} photos added', { count: selected.length }) : '';
        selected.forEach(function (file, index) {
          var row = document.createElement('div'); row.className = multi ? 'photo-preview photo-tile' : 'photo-preview';
          if (file.type.indexOf('image/') === 0) {
            var thumb = document.createElement('img'), url = URL.createObjectURL(file); urls.push(url);
            thumb.src = url; thumb.alt = t('Photo {number}', { number: index + 1 });
            var link = document.createElement('a'); link.href = url; link.target = '_blank'; link.rel = 'noopener';
            link.title = t('Tap to view full size'); link.appendChild(thumb); row.appendChild(link);
          } else if (file.type.indexOf('video/') === 0) {
            var vid = document.createElement('video'), vurl = URL.createObjectURL(file); urls.push(vurl);
            vid.src = vurl; vid.controls = true; vid.preload = 'metadata'; row.appendChild(vid);
          } else {
            var icon = document.createElement('span'); icon.className = 'upl-ic'; icon.textContent = '▶'; row.appendChild(icon);
          }
          if (!multi) {
            var meta = document.createElement('span'); meta.className = 'upl-meta';
            var name = document.createElement('b'); name.textContent = file.name;
            var size = document.createElement('small'); size.textContent = fmtSize(file.size);
            meta.append(name, size); row.appendChild(meta);
          }
          var remove = document.createElement('button'); remove.type = 'button'; remove.className = 'iconbtn sm upl-x';
          remove.textContent = '×'; remove.setAttribute('aria-label', t('Remove file') + ': ' + file.name); remove.title = t('Remove');
          remove.addEventListener('click', function () { selected.splice(index, 1); syncFiles(); renderFiles(); });
          row.appendChild(remove); prev.appendChild(row);
        });
        countAnswered();
      }
      function addFiles(files) {
        if (!multi) selected = [];
        Array.prototype.forEach.call(files, function (file) {
          if (!selected.some(function (old) { return old.name === file.name && old.size === file.size && old.lastModified === file.lastModified && old.type === file.type; })) selected.push(file);
        });
        syncFiles(); renderFiles();
      }
      input.addEventListener('change', function () {
        if (input.files && input.files.length) addFiles(input.files);
        else { syncFiles(); renderFiles(); }
      });
      if (multi && camera && cameraButton) {
        cameraButton.hidden = false;
        cameraButton.addEventListener('click', function () { camera.click(); });
        camera.addEventListener('change', function () { if (camera.files && camera.files.length) addFiles(camera.files); camera.value = ''; });
      }
      window.addEventListener('pagehide', function () {
        urls.forEach(function (url) { URL.revokeObjectURL(url); });
      });
    });

    // picking "fail" highlights the evidence that is then required (note, photo)
    iform.addEventListener('change', function (e) {
      var r = e.target;
      if (r.name && r.name.indexOf('p_') === 0) {
        var item = r.closest('.chk-item');
        if (item) item.classList.toggle('failed', r.value === 'fail' && r.checked);
      }
      countAnswered();
    });
    var allPass = document.getElementById('chk-pass');
    if (allPass) allPass.addEventListener('click', function () {
      iform.querySelectorAll('input[type=radio][name^="p_"][value="pass"]').forEach(function (r) { r.checked = true; });
      iform.querySelectorAll('.chk-item.failed').forEach(function (i) { i.classList.remove('failed'); });
      countAnswered();
    });
    countAnswered();
  }

  // ---- slide-over drawer: row-level detail opens here so tables stay compact
  var drawer = document.getElementById('drawer');
  if (drawer) {
    trapDialogFocus(drawer);
    var dBody = document.getElementById('drawer-body'), dTitle = document.getElementById('drawer-title'),
        dScrim = document.getElementById('drawer-scrim'), lastOpener = null;
    var closeDrawer = function () {
      if (drawer.open) drawer.close();
      drawer.hidden = true; dScrim.hidden = true; document.body.classList.remove('drawer-open');
      if (lastOpener) { lastOpener.focus(); lastOpener = null; }
    };
    document.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-drawer]');
      if (b) {
        var tpl = document.getElementById(b.getAttribute('data-drawer'));
        if (!tpl) return;
        e.preventDefault();
        dTitle.textContent = b.getAttribute('data-drawer-title') || '';
        dBody.innerHTML = '';
        dBody.appendChild(tpl.content.cloneNode(true));
        enhanceFields(dBody);
        drawer.hidden = false; dScrim.hidden = true; document.body.classList.add('drawer-open'); drawer.showModal();
        lastOpener = b;
        var f = dBody.querySelector('input,select,button') || document.getElementById('drawer-close');
        setTimeout(function () { f.focus(); }, 80);
        return;
      }
      if (e.target.closest('#drawer-close') || e.target === dScrim) closeDrawer();
    });
    drawer.addEventListener('close', closeDrawer);
    drawer.addEventListener('click', function (event) {
      var bounds = drawer.getBoundingClientRect();
      if (event.target === drawer && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) closeDrawer();
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !drawer.hidden) closeDrawer(); });
  }

  // ---- in-drawer AI explanation (replenishment "why this number"): answer in place, never navigate away
  document.addEventListener('submit', function (e) {
    var f = e.target.closest && e.target.closest('form[data-explain]');
    if (!f) return;
    e.preventDefault();
    var out = f.parentElement.querySelector('.explain-out');
    var btn = f.querySelector('button');
    f.classList.add('is-busy'); if (btn) btn.disabled = true;
    fetch(f.action, { method: 'POST', body: new FormData(f), credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!out) return;
        out.hidden = false;
        if (d.ok) { out.innerHTML = d.html; }
        else { out.textContent = d.error || t('The AI could not explain this.'); out.style.color = 'var(--bad)'; }
      })
      .catch(function () { if (out) { out.hidden = false; out.textContent = t('Could not reach the server.'); out.style.color = 'var(--bad)'; } })
      .finally(function () { f.classList.remove('is-busy'); if (btn) btn.disabled = false; });
  });

  // ---- chat window
  var fab = document.getElementById('chat-fab');
  if (!fab) return;
  var panel = document.getElementById('chat-panel'), log = document.getElementById('chat-log'),
      form = document.getElementById('chat-form'), input = document.getElementById('chat-input'),
      send = document.getElementById('chat-send'), sugg = document.getElementById('chat-sugg');
  var KEY = 'retailos-chat-' + document.documentElement.lang, turns = [], busy = false;

  function store() {
    try { sessionStorage.setItem(KEY, JSON.stringify({ turns: turns, html: log.innerHTML, open: !panel.hidden })); } catch (e) { }
  }
  function add(who, content, isHtml) {
    var d = document.createElement('div');
    d.className = 'msg ' + who;
    if (isHtml) d.innerHTML = content; else d.textContent = content;
    log.appendChild(d); log.scrollTop = log.scrollHeight;
    return d;
  }
  function hello() {
    add('bot', t('Hi, I can answer questions about stock, sales, purchase orders and issues, using only what is in the system. What would you like to know?'));
  }
  function openPanel(skipStore) {
    panel.hidden = false; fab.classList.add('on');
    if (!skipStore) store();
    setTimeout(function () { input.focus(); log.scrollTop = log.scrollHeight; }, 30);
  }
  function closePanel() { panel.hidden = true; fab.classList.remove('on'); store(); }
  function load() {
    try {
      var d = JSON.parse(sessionStorage.getItem(KEY) || 'null');
      if (d) { turns = d.turns || []; log.innerHTML = d.html || ''; if (turns.length) sugg.hidden = true; if (d.open) openPanel(true); }
    } catch (e) { }
    if (!log.children.length) hello();
  }

  fab.addEventListener('click', function () { if (panel.hidden) openPanel(); else closePanel(); });
  document.getElementById('chat-close').addEventListener('click', closePanel);
  document.getElementById('chat-clear').addEventListener('click', function () {
    turns = []; log.innerHTML = ''; sugg.hidden = false; hello(); store();
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !panel.hidden) closePanel(); });

  function ask(q) {
    q = (q || '').trim();
    if (!q || busy) return;
    busy = true; send.disabled = true; sugg.hidden = true;
    add('me', q);
    input.value = '';  // clear on send; otherwise the question sits in the field until the answer arrives
    var th = add('bot', '<span class="dots"><i></i><i></i><i></i></span>', true);
    th.appendChild(document.createTextNode(' ' + t('Reading your data, this takes 20 to 40 seconds...')));
    th.classList.add('thinking');
    var fd = new FormData();
    fd.append('question', q);
    fd.append('csrf', document.getElementById('chat-csrf').value);
    fd.append('history', JSON.stringify(turns.slice(-6)));
    var ctl = new AbortController(), to = setTimeout(function () { ctl.abort(); }, 180000);
    fetch('/assistant/chat', { method: 'POST', body: fd, credentials: 'same-origin', signal: ctl.signal })
      .then(function (r) {
        if (r.redirected || r.status === 401) throw new Error(t('Your session ended. Reload the page and sign in again.'));
        if (r.status === 403) throw new Error(t('Not allowed, or the page is out of date. Reload and try again.'));
        return r.json();
      })
      .then(function (d) {
        th.remove();
        if (d.ok) {
          add('bot', d.html, true);
          turns.push({ role: 'user', text: q }); turns.push({ role: 'assistant', text: d.text || '' });
        } else {
          add('bot', d.error || t('Something went wrong.')).classList.add('err');
        }
      })
      .catch(function (e) {
        th.remove();
        add('bot', e.name === 'AbortError' ? t('That took too long. Try a shorter question.') : (e.message || t('Could not reach the server.'))).classList.add('err');
      })
      .finally(function () { clearTimeout(to); busy = false; send.disabled = false; input.focus(); store(); });
  }
  form.addEventListener('submit', function (e) { e.preventDefault(); ask(input.value); });
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('[data-chat-ask]');
    if (a) { e.preventDefault(); if (panel.hidden) openPanel(); ask(a.getAttribute('data-chat-ask')); return; }
    var c = e.target.closest && e.target.closest('#chat-log .cite');
    if (c) e.preventDefault();
  });
  load();
})();
