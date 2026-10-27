/* Presentation enhancements only; saving and lookup stay with existing handlers. */
(function () {
 'use strict';
 var phone = matchMedia('(max-width:700px)');
 var zh = document.documentElement.lang === 'zh';
 var qty = document.getElementById('count-qty');
 var system = document.getElementById('count-system');
 var diff = document.getElementById('phone-count-diff');
 function difference() {
   if (!qty || !diff || !phone.matches) return;
   var current = Number(system.textContent.replace(/,/g, ''));
   var value = Number(qty.value);
   diff.textContent = qty.value !== '' && Number.isFinite(value) && Number.isFinite(current)
     ? (zh ? '差异：' : 'Difference: ') + (value - current).toLocaleString(undefined, {maximumFractionDigits:6, signDisplay:'exceptZero'}) : '';
 }
 if (qty && system && diff) {
   qty.addEventListener('input', difference);
   new MutationObserver(difference).observe(system, {childList:true, characterData:true, subtree:true});
 }
 var stock = document.querySelector('main:has(.phone-stock-list) form.filters');
 if (stock) {
   var advanced = document.createElement('button');
   advanced.type = 'button'; advanced.className = 'phone-only sec phone-stock-more';
   advanced.textContent = zh ? '更多筛选' : 'More filters';
   advanced.setAttribute('aria-expanded', 'false');
   advanced.addEventListener('click', function () {
     var open = stock.classList.toggle('phone-filters-open');
     advanced.setAttribute('aria-expanded', String(open));
   });
   stock.appendChild(advanced);
   stock.querySelectorAll('select').forEach(function (select) {
     select.addEventListener('change', function () { if (phone.matches) stock.submit(); });
   });
 }
 // Carry a stock-list location into the existing count creation form without submitting it.
 var location = document.querySelector('.counts-create select[name=location_id]');
 if (location && phone.matches) {
   var requested = new URLSearchParams(window.location.search).get('loc');
   if (requested && Array.from(location.options).some(function (o) { return o.value === requested; })) location.value = requested;
 }
 phone.addEventListener('change', function () {
   if (!phone.matches) {
     var work = document.getElementById('phone-work');
     if (work && work.open) work.close();
   }
 });
})();
