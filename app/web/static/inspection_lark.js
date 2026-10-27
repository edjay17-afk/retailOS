(function(){
'use strict';
var localeMessages=JSON.parse(document.getElementById('ui-messages').textContent);function tr(key){return localeMessages[key]||key;}
var root=document.getElementById('lark-inspection-check');if(!root)return;
var button=document.getElementById('lark-inspection-refresh'),message=document.getElementById('lark-inspection-message');
async function check(){button.disabled=true;message.textContent=tr('Checking Lark reports for this date...');try{
var body=new FormData();body.append('csrf',root.dataset.csrf);
var r=await fetch('/inspections/lark-reports?'+new URLSearchParams({on:root.dataset.day,loc:root.dataset.loc}),{method:'POST',body:body,credentials:'same-origin'});if(!r.ok)throw new Error();var data=await r.json();
(data.submitted||[]).forEach(function(id){var row=document.querySelector('[data-inspection-id="'+id+'"]');if(!row)return;var badge=row.querySelector('[data-inspection-status]');badge.textContent=tr('Submitted');badge.className='badge b-ok';row.querySelector('[data-inspection-result]').textContent=tr('Submitted via Lark; results not reviewed.');var link=row.querySelector('a[href^="/inspections/"]');if(link){link.textContent=tr('Add photos / video');link.classList.add('sec');}});
document.querySelectorAll('[data-lark-location]').forEach(function(el){el.replaceChildren();if(!data.available){el.textContent=tr('Not checked');return;}var reports=data.reports[el.dataset.larkLocation]||[];if(!reports.length){el.textContent=tr('No report found');return;}reports.forEach(function(report,index){if(index)el.appendChild(document.createElement('br'));var a=document.createElement('a');a.href=report.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=tr('View Lark report')+' - '+report.time;el.appendChild(a);var detail=document.createElement('div');detail.className='small mute';detail.textContent=tr('{count} attachments').replace('{count}',report.attachments);el.appendChild(detail);});});
message.textContent=data.available?tr('Lark checked at {time} - Philippine time. Matched daily inspections are marked Submitted.').replace('{time}',data.checked_at):data.message;
}catch(e){message.textContent=tr('Could not check Lark. Please retry.');document.querySelectorAll('[data-lark-location]').forEach(function(el){el.textContent=tr('Not checked');});}finally{button.disabled=false;}}
button.addEventListener('click',check);check();
})();
