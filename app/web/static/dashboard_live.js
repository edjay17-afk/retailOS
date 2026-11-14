(function(){
 'use strict';
 var panel=document.getElementById('live-store-sales');if(!panel)return;
 var timer,busy=false,stopped=false,seenInitial=false;
 var prevTotal=null,prevByStore={};
 var money=new Intl.NumberFormat('en-PH',{minimumFractionDigits:2,maximumFractionDigits:2});
 var whole=new Intl.NumberFormat('en-PH',{maximumFractionDigits:0});
 var note=panel.querySelector('.store-note'),normal=note?note.textContent:'';
 function put(el,value){if(el&&Number.isFinite(value)){var text=money.format(value);if(window.RollNumber)window.RollNumber.set(el,text);else if(el.textContent!==text)el.textContent=text;}}
 function putInt(el,value){if(el&&Number.isFinite(value)){var text=whole.format(value);if(window.RollNumber)window.RollNumber.set(el,text);else if(el.textContent!==text)el.textContent=text;}}
 function bump(el){if(!el)return;el.classList.remove('lv-bumped');void el.offsetWidth;el.classList.add('lv-bumped');clearTimeout(el._bumpT);el._bumpT=setTimeout(function(){el.classList.remove('lv-bumped');},1400);}
 function schedule(){clearTimeout(timer);if(!document.hidden&&!stopped)timer=setTimeout(refresh,30000);}
 async function refresh(){if(busy||document.hidden||stopped)return;busy=true;var controller=new AbortController(),timeout=setTimeout(function(){controller.abort();},15000);
 try{var response=await fetch('/dashboard/store-sales'+(window.SaleToast?window.SaleToast.query():''),{credentials:'same-origin',cache:'no-store',signal:controller.signal});
 if(response.redirected||response.status===401||response.status===403){stopped=true;throw new Error();}
 if(!response.ok)throw new Error();var data=await response.json();if(!Array.isArray(data.stores))throw new Error();
 data.stores.forEach(function(store){
  var id=Number(store.id),row=panel.querySelector('[data-store-id="'+id+'"]');if(!row)return;
  if(seenInitial&&Number.isFinite(store.today)&&prevByStore[id]!==undefined&&store.today>prevByStore[id])bump(row);
  prevByStore[id]=store.today;
  put(row.querySelector('.store-today'),store.today);put(row.querySelector('.store-yesterday'),store.yesterday);putInt(row.querySelector('.store-tx'),store.tx);
  if(data.today>0&&Number.isFinite(store.today)){
   var share=Math.round(store.today/data.today*100);
   var bar=row.querySelector('.lv-share i'),pctEl=row.querySelector('.lv-share-pct');
   if(bar)bar.style.width=share+'%';
   if(pctEl)pctEl.textContent=share+'%';
  }
 });
 if(seenInitial&&Number.isFinite(data.today)&&prevTotal!==null&&data.today>prevTotal)bump(document.getElementById('lv-total'));
 prevTotal=data.today;seenInitial=true;
 if(window.SaleToast)window.SaleToast.feed(data);
 if(Array.isArray(data.new_sales)&&data.new_sales.length){
  var last=data.new_sales[data.new_sales.length-1];
  var ticker=document.querySelector('.dash-live-ticker-text');
  if(ticker&&last){ticker.textContent=(last.store?last.store+' · ':'')+'PHP '+money.format(last.total);}
 }
 put(document.getElementById('live-sales-today'),data.today);put(document.getElementById('live-sales-yesterday'),data.yesterday);putInt(document.getElementById('live-tx-today'),data.tx);put(document.getElementById('live-avg-basket'),data.avg_basket);
 if(note)note.textContent=normal;
 window.dispatchEvent(new CustomEvent('ros:sales-live',{detail:data}));
 }catch(e){if(note)note.textContent=stopped?'Sign in again to resume automatic updates.':'Automatic update unavailable. Showing the last loaded sales; retrying shortly.';}
 finally{clearTimeout(timeout);busy=false;schedule();}}
 document.addEventListener('visibilitychange',function(){if(document.hidden)clearTimeout(timer);else refresh();});
 window.addEventListener('pagehide',function(){clearTimeout(timer);});
 window.addEventListener('pageshow',schedule);
 refresh();   // the first read only learns the latest sale, so nothing pops up for sales that were already there
 schedule();
})();
