(function(){
'use strict';
var localeMessages=JSON.parse(document.getElementById('ui-messages').textContent);
function tr(key){return localeMessages[key]||key;}

var root=document.getElementById('count-scan');if(!root)return;
var find=document.getElementById('count-find'),entry=document.getElementById('count-entry'),code=document.getElementById('count-code'),qty=document.getElementById('count-qty'),note=document.getElementById('count-note'),message=document.getElementById('count-message'),picked=null,busy=false,dirty=false;
var list=document.getElementById('count-products'),matches=[],active=-1,selected=0,timer,sequence=0;
function closeLookup(){clearTimeout(timer);sequence++;list.hidden=true;code.setAttribute('aria-expanded','false');code.removeAttribute('aria-activedescendant');active=-1;}
function choose(p){selected=p.id;code.value=p.name;closeLookup();find.requestSubmit();}
async function lookup(){
 var term=code.value.trim(),seq=++sequence;if(!term){closeLookup();return;}
 try{var r=await fetch(root.dataset.url+'/products?q='+encodeURIComponent(term),{credentials:'same-origin'});if(!r.ok)throw new Error();var rows=await r.json();if(seq!==sequence||busy)return;
 matches=rows;active=-1;list.replaceChildren();code.removeAttribute('aria-activedescendant');
 rows.forEach(function(p,i){var el=document.createElement('div');el.id='count-option-'+i;el.setAttribute('role','option');el.setAttribute('aria-selected','false');el.textContent=p.name;var meta=document.createElement('small');meta.textContent=[p.barcode,p.sku].filter(Boolean).join(' / ');el.appendChild(meta);el.addEventListener('click',function(){choose(p);});list.appendChild(el);});
 list.hidden=!rows.length;code.setAttribute('aria-expanded',String(!!rows.length));message.textContent=rows.length?'':tr('No matching StoreHub products.');
 }catch(e){if(seq===sequence){closeLookup();message.textContent=tr('Could not search products. Please retry.');}}
}
code.addEventListener('input',function(){selected=0;closeLookup();if(code.value.trim())timer=setTimeout(lookup,200);});
code.addEventListener('keydown',function(e){
 if(e.key==='Escape'){closeLookup();return;}
 if(!list.hidden&&(e.key==='ArrowDown'||e.key==='ArrowUp')){e.preventDefault();active=(active+(e.key==='ArrowDown'?1:-1)+matches.length)%matches.length;Array.from(list.children).forEach(function(el,i){el.setAttribute('aria-selected',String(i===active));});code.setAttribute('aria-activedescendant',list.children[active].id);list.children[active].scrollIntoView({block:'nearest'});}
 else if(e.key==='Enter'&&!list.hidden&&active>=0){e.preventDefault();choose(matches[active]);}
});
document.addEventListener('click',function(e){if(!find.contains(e.target))closeLookup();});
function lock(on){busy=on;root.querySelectorAll('button').forEach(function(b){b.disabled=on;});code.disabled=on;qty.disabled=on;note.disabled=on;}
async function post(path,values){var data=new FormData();data.append('csrf',root.dataset.csrf);Object.keys(values).forEach(function(k){data.append(k,values[k]);});var r=await fetch(root.dataset.url+path,{method:'POST',body:data,credentials:'same-origin'});var d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:tr('Could not complete the request. Please retry.'));return d;}
function reset(){selected=0;closeLookup();picked=null;dirty=false;entry.hidden=true;code.value='';code.focus();}
find.addEventListener('submit',async function(e){e.preventDefault();if(busy)return;if(dirty&&!confirm(tr('Discard this unsaved count?')))return;closeLookup();lock(true);message.textContent=tr('Fetching current stock from StoreHub...');entry.hidden=true;picked=null;try{var p=await post('/scan',{code:code.value.trim(),product_id:selected});selected=0;picked=p;document.getElementById('count-product').textContent=p.name;document.getElementById('count-system').textContent=p.system;qty.value=p.counted==null?'':p.counted;note.value=p.note||'';dirty=false;entry.hidden=false;message.textContent='';}catch(e){message.textContent=e.message;}finally{lock(false);if(!picked)lookup();if(picked){qty.focus();qty.select();}else code.focus();}});
entry.addEventListener('input',function(){dirty=true;});
entry.addEventListener('submit',async function(e){e.preventDefault();if(busy||!picked||!entry.reportValidity())return;var values={product_id:picked.id,counted:qty.value,note:note.value};lock(true);message.textContent=tr('Saving...');try{var d=await post('/scan-save',values);dirty=false;entry.hidden=true;picked=null;selected=0;closeLookup();code.value='';message.textContent=tr('Saved {name}: {counted} counted. Scan the next product.').replace('{name}',d.name).replace('{counted}',d.counted);try{var r=await fetch(root.dataset.url,{credentials:'same-origin'});if(r.ok){var doc=new DOMParser().parseFromString(await r.text(),'text/html');var review=document.getElementById('count-review');review.innerHTML=doc.getElementById('count-review').innerHTML;var recent=document.getElementById('phone-count-recent'),updated=doc.getElementById('phone-count-recent');if(recent&&updated)recent.innerHTML=updated.innerHTML;}}catch(ignore){} }catch(e){message.textContent=e.message;}finally{lock(false);if(picked)qty.focus();else code.focus();}});
document.getElementById('count-cancel').addEventListener('click',function(){if(!dirty||confirm(tr('Discard this unsaved count?')))reset();});
window.addEventListener('beforeunload',function(e){if(dirty||busy){e.preventDefault();e.returnValue='';}});
})();
