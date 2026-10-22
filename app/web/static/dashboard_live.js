(function(){
 'use strict';
 var panel=document.getElementById('live-store-sales');if(!panel)return;
 var timer,busy=false,stopped=false;
 var money=new Intl.NumberFormat('en-PH',{minimumFractionDigits:2,maximumFractionDigits:2});
 var note=panel.querySelector('.store-note'),normal=note.textContent;
 function put(el,value){if(el&&Number.isFinite(value)){var text=money.format(value);if(el.textContent!==text)el.textContent=text;}}
 function schedule(){clearTimeout(timer);if(!document.hidden&&!stopped)timer=setTimeout(refresh,30000);}
 async function refresh(){if(busy||document.hidden||stopped)return;busy=true;var controller=new AbortController(),timeout=setTimeout(function(){controller.abort();},15000);
 try{var response=await fetch('/dashboard/store-sales',{credentials:'same-origin',cache:'no-store',signal:controller.signal});
 if(response.redirected||response.status===401||response.status===403){stopped=true;throw new Error();}
 if(!response.ok)throw new Error();var data=await response.json();if(!Array.isArray(data.stores))throw new Error();
 data.stores.forEach(function(store){var row=panel.querySelector('[data-store-id="'+Number(store.id)+'"]');if(!row)return;put(row.querySelector('.store-today'),store.today);put(row.querySelector('.store-yesterday'),store.yesterday);});
 put(document.getElementById('live-sales-today'),data.today);put(document.getElementById('live-sales-yesterday'),data.yesterday);note.textContent=normal;
 }catch(e){note.textContent=stopped?'Sign in again to resume automatic updates.':'Automatic update unavailable. Showing the last loaded sales; retrying shortly.';}
 finally{clearTimeout(timeout);busy=false;schedule();}}
 document.addEventListener('visibilitychange',function(){if(document.hidden)clearTimeout(timer);else refresh();});
 window.addEventListener('pagehide',function(){clearTimeout(timer);});
 window.addEventListener('pageshow',schedule);
 schedule();
})();
