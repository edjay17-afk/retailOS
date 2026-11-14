/* StoreHub history: typing in the search box filters the list by itself (a short pause after the last key), with no Enter and no page reload. */
(function () {
  'use strict';
  var inputs = Array.prototype.slice.call(document.querySelectorAll('input[name=q][type=search]'));
  if (!inputs.length) return;
  var parts = ['.se-head-actions', '.se-summary', '.se-hist', '.se-phone-table', '.pager'];
  var timer = null, request = 0, controller = null;

  function apply(form) {
    if (timer) { clearTimeout(timer); timer = null; }
    var url = (form.getAttribute('action') || window.location.pathname).split('?')[0] + '?' + new URLSearchParams(new FormData(form)).toString();
    var current = ++request;
    if (controller) controller.abort();
    controller = new AbortController();
    form.setAttribute('aria-busy', 'true');
    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin', signal: controller.signal })
      .then(function (response) { if (!response.ok) throw new Error('Search failed'); return response.text(); })
      .then(function (html) {
        if (current !== request) return;
        var doc = new DOMParser().parseFromString(html, 'text/html');
        if (!doc.querySelector('.se-hist') && !doc.querySelector('.se-phone-table')) throw new Error('Search failed');
        parts.forEach(function (selector) {
          var old = document.querySelector(selector), fresh = doc.querySelector(selector);
          if (old && fresh) old.replaceWith(fresh);
        });
        inputs.forEach(function (other) { if (other.form !== form) other.value = form.elements.q.value; });   // the phone and desktop boxes stay in step
        history.replaceState(null, '', url);
      })
      .catch(function (error) { if (error.name !== 'AbortError') window.location.assign(url); })
      .finally(function () { if (current === request) form.removeAttribute('aria-busy'); });
  }

  inputs.forEach(function (input) {
    var form = input.form;
    input.addEventListener('input', function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () { apply(form); }, 350);
    });
    form.addEventListener('submit', function (event) { event.preventDefault(); apply(form); });
  });
})();
