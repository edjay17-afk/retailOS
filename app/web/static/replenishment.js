(function () {
  'use strict';
  var messages = document.currentScript.dataset;
  var mobile = window.matchMedia('(max-width:700px)');
  function setGuideMode() {
    document.querySelectorAll('.rep-guide,.rep-policy').forEach(function (el) { el.open = !mobile.matches; });
  }
  setGuideMode();
  mobile.addEventListener('change', setGuideMode);
  var bulk = document.getElementById('bulk');
  var all = document.getElementById('repl-select-all');
  var bar = document.getElementById('repl-selection');
  if (!bulk || !all || !bar) return;
  var checks = Array.from(document.querySelectorAll('.rc[form="bulk"]'));
  var forms = Array.from(document.querySelectorAll('.repl-quantity'));
  function dirty(form) { return Number(form.elements.qty.value) !== Number(form.dataset.saved); }
  function update() {
    var selected = checks.filter(function (c) { return c.checked; });
    var unsaved = forms.some(function (f) {
      return selected.some(function (c) { return c.value === f.dataset.recId; }) && (dirty(f) || f.dataset.saving === 'true');
    });
    bar.hidden = selected.length === 0;
    document.getElementById('repl-selected-count').textContent = selected.length;
    document.getElementById('repl-proceed').disabled = !selected.length || unsaved;
    document.getElementById('repl-save-hint').hidden = !unsaved;
    all.checked = checks.length > 0 && selected.length === checks.length;
    all.indeterminate = selected.length > 0 && selected.length < checks.length;
    all.disabled = !checks.length;
  }
  checks.forEach(function (c) { c.addEventListener('change', update); });
  all.addEventListener('change', function () {
    checks.forEach(function (c) { c.checked = all.checked; });
    update();
  });
  forms.forEach(function (form) {
    var status = form.querySelector('.repl-save-status');
    var button = form.elements.namedItem('saveButton');
    form.addEventListener('input', function () {
      status.textContent = dirty(form) ? messages.unsaved : '';
      update();
    });
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      if (form.dataset.saving === 'true' || !form.reportValidity()) return;
      form.dataset.saving = 'true';
      button.disabled = true;
      status.textContent = messages.saving;
      update();
      try {
        var response = await fetch(form.getAttribute('action'), {
          method: 'POST', body: new FormData(form), credentials: 'same-origin',
          headers: {'X-Requested-With': 'fetch'}
        });
        var result = await response.json();
        if (!response.ok) throw new Error(result.error || messages.failed);
        form.dataset.saved = result.quantity;
        status.textContent = dirty(form) ? messages.unsaved : result.message;
      } catch (error) {
        status.textContent = error.message || messages.failed;
      } finally {
        form.dataset.saving = 'false';
        button.disabled = false;
        update();
      }
    });
  });
  bulk.addEventListener('submit', function (event) {
    update();
    if (document.getElementById('repl-proceed').disabled) event.preventDefault();
  });
  window.addEventListener('pageshow', update);
  window.addEventListener('beforeunload', function (event) {
    if (forms.some(function (form) { return dirty(form) || form.dataset.saving === 'true'; })) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  update();
})();
