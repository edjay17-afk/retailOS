/* Decimal arithmetic uses integers and a decimal scale, never floating-point money. */
(function(root) {
  'use strict';
  function decimal(text) {
    text = String(text).trim();
    if (text.length > 200 || !/^\d+(\.\d+)?$/.test(text)) throw Error('decimal');
    const parts = text.split('.');
    return {n: BigInt(parts.join('')), s: (parts[1] || '').length};
  }
  function add(a,b,subtract=false) {
    const s=Math.max(a.s,b.s);
    return {n:a.n*10n**BigInt(s-a.s)+(subtract?-1n:1n)*b.n*10n**BigInt(s-b.s),s};
  }
  function multiply(a,b){return {n:a.n*b.n,s:a.s+b.s};}
  function format(a,minimum=2){
    const negative=a.n<0n;
    let text=(negative?-a.n:a.n).toString().padStart(a.s+1,'0');
    let whole=a.s?text.slice(0,-a.s):text;
    let fraction=a.s?text.slice(-a.s).replace(/0+$/,''):'';
    fraction=fraction.padEnd(minimum,'0');
    return (negative?'-':'')+whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction?'.'+fraction:'');
  }
  const api={decimal,add,multiply,format};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  if(typeof document==='undefined') return;
  const form=document.getElementById('document-editor');
  if(!form) return;
  const data=JSON.parse(document.getElementById('document-data').textContent);
  const messages=JSON.parse(document.getElementById('document-messages').textContent);
  const container=document.getElementById('document-lines');
  const status=document.getElementById('document-status');
  const pickers=[];
  let revision=data.revision,dirty=false,busy=false;
  function changed(){dirty=true;status.textContent=messages.unsaved;totals();}
  function lineData(article){const line={};article.querySelectorAll('[data-line]').forEach(input=>line[input.dataset.line]=input.value);return line;}
  function positionResults(search,results){
    const rect=search.getBoundingClientRect(),width=Math.min(Math.max(rect.width,260),window.innerWidth-16);
    results.style.width=width+'px';
    results.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';
    const below=window.innerHeight-rect.bottom-8;
    const top=Math.max(8,Math.min(below>=160?rect.bottom+4:rect.top-244,window.innerHeight-248));
    results.style.top=top+'px';
    results.style.maxHeight=Math.max(80,Math.min(240,window.innerHeight-top-8))+'px';
  }
  function totals(){
    try{
      let sum=decimal('0');
      container.querySelectorAll('.document-item').forEach(article=>{
        const line=lineData(article),amount=multiply(decimal(line.quantity),decimal(line.cost));
        const output=article.querySelector('[data-line-amount]');
        output.textContent=format(amount);output.title=output.textContent;
        sum=add(sum,amount);
      });
      const tax=form.querySelector('[data-header="tax"]'),discount=form.querySelector('[data-header="discount"]');
      document.getElementById('document-subtotal').textContent=format(sum);
      document.getElementById('document-total').textContent=format(add(add(sum,decimal(tax?tax.value:'0')),decimal(discount?discount.value:'0'),true));
      return true;
    }catch(e){document.getElementById('document-total').textContent='—';return false;}
  }
  function renumber(){container.querySelectorAll('.document-item').forEach((article,index)=>{
    article.querySelector('.document-row-number').textContent=String(index+1);
    article.querySelectorAll('input').forEach(input=>{
      const field=input.dataset.line;
      input.id='document-line-'+index+'-'+field;
      input.parentElement.htmlFor=input.id;
    });
  });}
  function row(line){
    const article=document.getElementById('document-row-template').content.firstElementChild.cloneNode(true);
    article.querySelectorAll('[data-line]').forEach(input=>input.value=line[input.dataset.line]||'');
    const search=article.querySelector('[data-line="name"]'),results=article.querySelector('.document-product-results');
    // Keep the dropdown outside the scrolling table so it remains clickable at its edges.
    form.append(results);
    const picker={search,results};pickers.push(picker);
    article.querySelector('.document-remove').addEventListener('click',()=>{
      results.remove();pickers.splice(pickers.indexOf(picker),1);article.remove();renumber();changed();
    });
    let timer,controller;
    search.addEventListener('input',()=>{
      // A changed description must not silently keep an ID belonging to a different product.
      article.querySelector('[data-line="product_id"]').value='';
      article.querySelector('[data-line="sku"]').value='';
      clearTimeout(timer);if(controller)controller.abort();results.hidden=true;
      const query=search.value.trim();
      if(query.length<2)return;
      timer=setTimeout(async()=>{
        controller=new AbortController();
        try{
          const response=await fetch(form.dataset.base+'/products?q='+encodeURIComponent(query),{signal:controller.signal,headers:{Accept:'application/json'}});
          if(!response.ok)throw Error('search');
          const products=await response.json();
          if(!article.isConnected || search.value.trim()!==query)return;
          results.replaceChildren();
          products.forEach(product=>{
            const button=document.createElement('button');button.type='button';button.textContent=product.name+(product.sku?' · '+product.sku:'');
            function chooseProduct(){
              ['name','sku','product_id'].forEach(key=>article.querySelector('[data-line="'+key+'"]').value=product[key]);
              // Preserve an existing supplier/transfer cost; catalog cost is only a starting value for a new row.
              const cost=article.querySelector('[data-line="cost"]');if(!cost.value || cost.value==='0')cost.value=product.cost;
              results.hidden=true;changed();
            }
            // Choose before focus leaves the search field; Enter still uses the click handler.
            button.addEventListener('pointerdown',event=>{event.preventDefault();chooseProduct();});
            button.addEventListener('click',chooseProduct);results.append(button);
          });results.hidden=!products.length;
          if(products.length)positionResults(search,results);
        }catch(e){if(e.name!=='AbortError')status.textContent=messages.searchError;}
      },250);
    });
    article.addEventListener('focusout',event=>{
      if(event.relatedTarget && !article.contains(event.relatedTarget) && !results.contains(event.relatedTarget))results.hidden=true;
    });
    search.addEventListener('keydown',event=>{if(event.key==='Escape')results.hidden=true;});
    container.append(article);renumber();
    return article;
  }
  data.lines.forEach(row);totals();
  function repositionResults(){pickers.forEach(({search,results})=>{if(!results.hidden)positionResults(search,results);});}
  document.addEventListener('pointerdown',event=>{pickers.forEach(({search,results})=>{
    if(!search.parentElement.contains(event.target) && !results.contains(event.target))results.hidden=true;
  });});
  document.addEventListener('scroll',event=>{
    if(event.target instanceof Element && event.target.closest('.document-product-results'))return;
    repositionResults();
  },true);
  window.addEventListener('resize',repositionResults);
  document.getElementById('add-document-row').addEventListener('click',()=>{
    const article=row({name:'',quantity:'1',cost:'0'});changed();article.querySelector('input').focus();
  });
  form.addEventListener('input',changed);
  form.querySelectorAll('[data-header]').forEach(input=>{input.id='document-header-'+input.dataset.header;input.parentElement.htmlFor=input.id;});
  async function save(){
    if(busy)return false;
    const invalid=form.querySelector('input:invalid,textarea:invalid');
    if(invalid){const details=invalid.closest('details');if(details)details.open=true;}
    if(!form.reportValidity() || !totals() || !container.children.length){status.textContent=messages.invalid;return false;}
    const header=Object.assign({},data.header);
    form.querySelectorAll('[data-header]').forEach(input=>header[input.dataset.header]=input.value);
    const payload={header,lines:Array.from(container.children).map(lineData),revision,csrf:form.elements.csrf.value};
    busy=true;status.textContent=messages.saving;
    const controls=Array.from(form.querySelectorAll('input,textarea,button'));controls.forEach(control=>control.disabled=true);
    try{
      const response=await fetch(form.dataset.base+'/edit',{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify(payload)});
      const result=await response.json();
      if(!response.ok)throw Error(result.detail||messages.failed);
      revision=result.revision;dirty=false;status.textContent=result.message||messages.saved;return true;
    }catch(e){status.textContent=e.message||messages.failed;return false;}
    finally{busy=false;controls.forEach(control=>control.disabled=false);}
  }
  form.addEventListener('submit',async event=>{event.preventDefault();await save();});
  form.querySelectorAll('[data-document-export]').forEach(button=>button.addEventListener('click',async()=>{
    const mode=button.dataset.documentExport;
    // Open the print tab in the click event so browser popup controls allow it.
    const printTab=mode==='print'?window.open('about:blank','_blank'):null;
    if(printTab)printTab.opener=null;
    if(!(await save())){if(printTab)printTab.close();return;}
    if(mode==='print'){
      if(printTab)printTab.location.href=form.dataset.base+'/print';
      else window.location.href=form.dataset.base+'/print';
    }else window.location.href=form.dataset.base+'/export?mode='+mode;
  }));
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
})(typeof globalThis!=='undefined'?globalThis:this);
