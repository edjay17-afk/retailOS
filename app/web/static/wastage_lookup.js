(function(){
 'use strict';
 var input=document.querySelector('[data-product-lookup]')||document.getElementById('waste-product'),id=document.getElementById('waste-product-id'),list=document.getElementById('waste-products'),status=document.getElementById('waste-lookup-status');
 var items=[],active=-1,timer,sequence=0;
 function close(){list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1;}
 function pick(p){sequence++;clearTimeout(timer);id.value=p.id;input.value=p.name;status.textContent=p.barcode||p.sku||'';close();}
 function mark(){Array.from(list.children).forEach(function(el,i){el.setAttribute('aria-selected',i===active?'true':'false');});if(active>=0){input.setAttribute('aria-activedescendant','waste-option-'+active);list.children[active].scrollIntoView({block:'nearest'});}}
 async function search(){var mine=++sequence,q=input.value.trim();if(!q){close();status.textContent='';return;}status.textContent='Searching products?';
 try{var response=await fetch((input.dataset.productLookup||'/wastage/products')+'?q='+encodeURIComponent(q)+'&loc='+encodeURIComponent(input.form.elements.location_id?input.form.elements.location_id.value:'0'),{credentials:'same-origin'});if(!response.ok)throw new Error();var results=await response.json();if(mine!==sequence)return;items=results;list.textContent='';active=-1;
 var exact=items.filter(function(p){return p.barcode===q||p.sku===q;});if(exact.length===1){pick(exact[0]);return;}
 status.textContent=items.length?'':'No matching POS products.';
 items.forEach(function(p,i){var option=document.createElement('div');option.id='waste-option-'+i;option.setAttribute('role','option');option.setAttribute('aria-selected','false');var name=document.createElement('b');name.textContent=p.name;option.appendChild(name);var code=document.createElement('small');code.textContent=[p.barcode,p.sku].filter(Boolean).join(' ? ');option.appendChild(code);option.addEventListener('mousedown',function(e){e.preventDefault();});option.addEventListener('click',function(){pick(p);});list.appendChild(option);});
 list.hidden=!items.length;input.setAttribute('aria-expanded',items.length?'true':'false');
 }catch(e){if(mine!==sequence)return;close();status.textContent='Could not load products. Type again to retry.';}}
 if(input.form.elements.location_id)input.form.elements.location_id.addEventListener('change',function(){sequence++;clearTimeout(timer);id.value='0';input.value='';close();status.textContent='';});
 input.form.addEventListener('reset',function(){id.value='0';sequence++;clearTimeout(timer);close();status.textContent='';items=[];});
 input.addEventListener('input',function(){sequence++;id.value='0';clearTimeout(timer);close();timer=setTimeout(search,200);});
 input.addEventListener('keydown',function(e){if(e.key==='Escape'){sequence++;clearTimeout(timer);close();return;}if(e.key==='Enter'){e.preventDefault();if(!list.hidden&&active>=0)pick(items[active]);else if(!Number(id.value)){clearTimeout(timer);search();}else input.form.elements.qty.focus();return;}if(!list.hidden&&(e.key==='ArrowDown'||e.key==='ArrowUp')){e.preventDefault();active=(active+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;mark();}});
 document.addEventListener('click',function(e){if(!e.target.closest('.waste-product-control')){sequence++;clearTimeout(timer);close();}});
})();
