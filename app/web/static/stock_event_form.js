/* Record a change: the "How it will look" card follows what is typed, and the photo box shows the chosen file's name. */
(function () {
  'use strict';
  var root = document.querySelector('.sef'); if (!root) return;
  var out = root.dataset.kind === 'write_off';
  var qty = document.getElementById('event-qty'), search = document.getElementById('event-search'), place = document.getElementById('event-location_id');
  var prevQty = document.getElementById('sef-prev-qty'), prevProduct = document.getElementById('sef-prev-product'), prevMeta = document.getElementById('sef-prev-meta');
  var photo = document.getElementById('event-photo'), fileName = document.getElementById('sef-file-name'), fileDefault = fileName ? fileName.textContent : '';
  var typedProduct = prevProduct ? prevProduct.textContent : '';
  function reason() { var r = root.querySelector('input[name=reason]:checked'); return r ? r.nextElementSibling.textContent : ''; }
  function sync() {
    var n = qty ? qty.value.trim() : '';
    if (prevQty) prevQty.textContent = (out ? '−' : '+') + (n || '0');
    if (prevProduct && search && search.tagName === 'INPUT') prevProduct.textContent = search.value.trim() || typedProduct;
    var where = place && place.selectedOptions[0] ? place.selectedOptions[0].textContent.replace('The Daily Centro - ', '') : '';
    if (prevMeta) prevMeta.textContent = [where, reason()].filter(Boolean).join(' · ');
  }
  root.addEventListener('input', sync); root.addEventListener('change', sync);
  if (photo && fileName) photo.addEventListener('change', function () { fileName.textContent = photo.files && photo.files[0] ? photo.files[0].name : fileDefault; });
  sync();
})();
