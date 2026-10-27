/* Draft persistence feedback for transfer sheets and purchase orders. */
(function () {
  'use strict';
  window.RetailDraft = function (options) {
    var status = document.getElementById('draft-save-status'), baseline = new WeakMap(), pending = false, uncertain = false, failed = false, leaving = false;
    var disabled = new Map();
    function fields() { return Array.from(document.querySelectorAll(options.lines)); }
    function capture() { fields().forEach(function (el) { baseline.set(el, el.value); }); refresh(); }
    function dirty() { return fields().some(function (el) { return baseline.has(el) && el.value !== baseline.get(el); }) || options.extra(); }
    function refresh() {
      if (!status) return;
      var state = pending ? 'saving' : uncertain || failed ? 'error' : dirty() ? 'unsaved' : 'saved';
      status.dataset.state = state;
      status.textContent = {saving:'Saving draft...', error:uncertain ? 'Save not confirmed. Reload the draft before retrying.' : 'Not saved. Check the entry and try again.', unsaved:'Unsaved changes', saved:'Draft saved'}[state];
      document.querySelectorAll(options.final + ' button').forEach(function (b) { b.disabled = pending || uncertain || failed || dirty() || !fields().length; });
    }
    function lock() {
      document.querySelectorAll(options.controls).forEach(function (el) { if (!disabled.has(el)) disabled.set(el, el.disabled); el.disabled = true; });
    }
    function unlock() { disabled.forEach(function (value, el) { if (el.isConnected) el.disabled = value; }); disabled.clear(); }
    document.addEventListener('input', refresh);
    document.addEventListener('change', refresh);
    document.addEventListener('click', function () { queueMicrotask(refresh); });
    document.addEventListener('close', refresh, true);
    window.addEventListener('beforeunload', function (event) {
      if (!leaving && (pending || uncertain || failed || dirty())) { event.preventDefault(); event.returnValue = ''; }
    });
    document.addEventListener('submit', function (event) {
      var form = event.target;
      if (!form.matches(options.final)) return;
      if (pending || uncertain || failed || dirty()) {
        event.preventDefault(); event.stopImmediatePropagation(); refresh();
      } else { leaving = true; status.textContent = 'Saving...'; }
    }, true);
    document.addEventListener('submit', function (event) {
      if (!event.defaultPrevented && /\/(cancel|discard|delete)$/.test(event.target.action || '')) leaving = true;
    });
    return {
      capture: capture, refresh: refresh,
      post: function (url, data) {
        if (pending || uncertain) return Promise.resolve({ok:false,error:uncertain ? 'Reload the draft before retrying; the previous request may have saved.' : 'Please wait for the draft to finish saving.'});
        pending = true; failed = false; refresh(); lock();
        return fetch(url, {method:'POST',body:data,credentials:'same-origin'}).then(function (response) {
          if (response.redirected) throw new Error('Session ended. Reload and sign in to check the draft.');
          return response.json();
        }).catch(function () {
          uncertain = true;
          return {ok:false,error:'Could not confirm the save. Your input is still here. Reload the draft before retrying to avoid duplicates.'};
        }).then(function (result) {
          pending = false; failed = !result.ok; unlock(); refresh(); return result;
        });
      }
    };
  };
})();
