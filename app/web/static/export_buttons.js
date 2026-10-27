/* Download buttons that belong to a filter form: they send the same filters, but only when clicked.
   (A submit button placed before the search box would be the one Enter presses, so these are plain buttons.) */
document.querySelectorAll('[data-export-form]').forEach(function (b) {
  b.addEventListener('click', function () {
    var f = document.getElementById(b.dataset.exportForm);
    if (!f) return;
    var h = document.createElement('input');
    h.type = 'hidden'; h.name = 'export'; h.value = b.dataset.export || 'csv'; f.appendChild(h); f.submit();
    setTimeout(function () { h.remove(); }, 800);
  });
});
