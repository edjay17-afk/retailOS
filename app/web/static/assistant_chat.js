(function () {
  'use strict';
  var panel = document.getElementById('chat-panel');
  if (!panel) return;
  var fab = document.getElementById('chat-fab'), log = document.getElementById('chat-log'),
      input = document.getElementById('chat-input'), form = document.getElementById('chat-form'),
      send = document.getElementById('chat-send'), stop = document.getElementById('chat-stop'),
      suggestions = document.getElementById('chat-sugg'), scope = document.getElementById('chat-context'),
      useContext = document.getElementById('chat-use-context'), historyList = document.getElementById('chat-history-list'),
      mount = document.getElementById('assistant-chat-mount'), parent = 0, busy = false, loading = false, controller = null, context = {}, contextRequest = 0,
      key = 'retailos-chat-v2-' + panel.dataset.user + '-' + document.documentElement.lang;
  // Never restore the old shared HTML cache across users.
  try { sessionStorage.removeItem('retailos-chat-' + document.documentElement.lang); } catch (_) {}
  if (mount) { mount.appendChild(panel); panel.classList.add('chat-workspace'); panel.hidden = false; fab.hidden = true; }
  function save() {
    try { sessionStorage.setItem(key, JSON.stringify({parent: parent, open: !panel.hidden, draft: input.value})); } catch (_) {}
  }
  function message(who, content, html) {
    var el = document.createElement('div'); el.className = 'msg ' + who;
    if (html) el.innerHTML = content; else el.textContent = content;
    log.appendChild(el); log.scrollTop = log.scrollHeight; return el;
  }
  function hello() {
    var welcome = message('bot chat-welcome', ''), brand = fab.querySelector('img').cloneNode(true);
    var title = document.createElement('h2'), detail = document.createElement('p');
    title.textContent = 'What can I help you with?';
    detail.textContent = 'Explore sales, check stock, and plan your next step. Answers backed by your store data.';
    welcome.append(brand, title, detail);
  }
  var mobile = window.matchMedia('(max-width: 640px)');
  function syncPanel() {
    var modal = !mount && !panel.hidden && mobile.matches;
    document.body.classList.toggle('chat-mobile-open', modal);
    panel.setAttribute('role', modal ? 'dialog' : 'region');
    if (modal) panel.setAttribute('aria-modal', 'true'); else panel.removeAttribute('aria-modal');
    fab.setAttribute('aria-expanded', String(!panel.hidden));
  }
  mobile.addEventListener('change', syncPanel);
  function show() { panel.hidden = false; fab.classList.add('on'); syncPanel(); input.focus({preventScroll: true}); save(); }
  function hide() { if (mount) return; panel.hidden = true; fab.classList.remove('on'); syncPanel(); fab.focus({preventScroll: true}); save(); }
  function pageURL() {
    if (!useContext.checked) return '#';
    if (mount) return context.url || '';
    var url = new URL(window.location.href), filters = document.querySelector('#product-sales-filters, .sales-filters, .content form.filters');
    if (filters) { url.search = new URLSearchParams(new FormData(filters)).toString(); }
    return url.pathname + url.search;
  }
  function paintContext(page) {
    context = page || {};
    scope.textContent = useContext.checked ? ((context.chips || []).join(' · ') || 'Your accessible stores') : 'Page context off';
    scope.title = scope.textContent;
    if (!parent && context.suggestions) {
      suggestions.replaceChildren();
      context.suggestions.forEach(function (q) { var b = document.createElement('button'); b.type = 'button'; b.className = 'chip'; b.dataset.chatAsk = q; b.textContent = q; suggestions.appendChild(b); });
    }
  }
  async function json(url, opts) {
    var r = await fetch(url, Object.assign({credentials: 'same-origin'}, opts || {}));
    if (r.redirected || r.status === 401) throw new Error('Your session ended. Sign in again.');
    if (!r.ok) throw new Error(r.status === 404 ? 'Conversation not found.' : 'This request is unavailable. Refresh and try again.');
    return r.json();
  }
  async function refreshContext() {
    if (!useContext.checked) { scope.textContent = 'Page context off'; return; }
    var requestId = ++contextRequest;
    try { var data = await json('/assistant/context?url=' + encodeURIComponent(pageURL())); if (requestId === contextRequest && useContext.checked) paintContext(data.page); }
    catch (_) { scope.textContent = 'Page context unavailable'; }
  }
  async function resume(id, openAfter) {
    if (busy || loading) return;
    loading = true; send.disabled = true;
    try {
      var data = await json('/assistant/conversations/' + encodeURIComponent(id));
      log.replaceChildren(); parent = data.id;
      data.messages.forEach(function (m) { message('me', m.question); if (m.html) message('bot', m.html, true); else message('bot err', 'This answer could not be completed. Ask again to retry.'); });
      if (mount) paintContext(data.page);
      suggestions.hidden = true; historyList.hidden = true; if (openAfter !== false) show(); save();
    } catch (e) { message('bot err', e.message); }
    finally { loading = false; send.disabled = false; }
  }
  async function listHistory() {
    if (busy || loading) return;
    historyList.hidden = !historyList.hidden;
    if (historyList.hidden) return;
    historyList.textContent = 'Loading conversations…';
    try {
      var data = await json('/assistant/conversations'); historyList.replaceChildren();
      if (!data.conversations.length) historyList.textContent = 'No saved conversations yet.';
      data.conversations.forEach(function (chat) { var b = document.createElement('button'); b.type = 'button'; b.className = 'assistant-thread'; b.dataset.chatResume = chat.id; b.textContent = chat.title; historyList.appendChild(b); });
    } catch (e) { historyList.textContent = e.message; }
  }
  async function updateThreads() {
    var aside = document.querySelector('.assistant-conversations'); if (!aside) return;
    try {
      var data = await json('/assistant/conversations');
      aside.querySelectorAll('.assistant-thread, .small.mute').forEach(function (el) { el.remove(); });
      data.conversations.forEach(function (chat) { var b = document.createElement('button'); b.type = 'button'; b.className = 'assistant-thread'; b.dataset.chatResume = chat.id; b.textContent = chat.title; aside.appendChild(b); });
    } catch (_) {}
  }
  function newChat() {
    if (busy || loading) return;
    parent = 0; log.replaceChildren(); input.value = ''; suggestions.hidden = false; historyList.hidden = true;
    if (mount) context = {};
    hello(); refreshContext(); show(); save();
  }
  function failure(text, question) {
    var el = message('bot err', text), b = document.createElement('button');
    b.type = 'button'; b.className = 'sec sm chat-retry'; b.textContent = 'Retry';
    b.addEventListener('click', function () { ask(question); }); el.appendChild(b);
  }
  async function ask(question) {
    question = String(question || '').trim(); if (!question || busy || loading) return;
    busy = true; send.disabled = true; stop.hidden = false; log.querySelectorAll('.chat-welcome').forEach(function (el) { el.remove(); }); suggestions.hidden = true; historyList.hidden = true;
    document.getElementById('chat-clear').disabled = true;
    log.setAttribute('aria-busy', 'true'); message('me', question); input.value = '';
    var waiting = message('bot thinking', '');
    var dots = document.createElement('span'), statusText = document.createElement('span');
    dots.className = 'chat-typing-dots'; dots.setAttribute('aria-hidden', 'true');
    for (var dot = 0; dot < 3; dot++) dots.appendChild(document.createElement('i'));
    statusText.className = 'chat-typing-status'; statusText.textContent = 'Sending your question...';
    statusText.setAttribute('role', 'status'); statusText.setAttribute('aria-live', 'polite');
    waiting.appendChild(dots); waiting.appendChild(statusText);
    var fd = new FormData(); fd.append('question', question); fd.append('parent_id', parent);
    fd.append('page_url', pageURL()); fd.append('csrf', document.getElementById('chat-csrf').value);
    controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 240000), gotResult = false;
    function event(data) {
      if (data.type === 'progress') { statusText.textContent = data.message + '…'; return; }
      if (data.type !== 'result') return;
      gotResult = true; waiting.remove();
      if (data.ok) { message('bot', data.html, true); parent = data.id; paintContext(data.page); save(); updateThreads(); }
      else failure(data.error || 'The assistant could not answer.', question);
    }
    try {
      var r = await fetch('/assistant/chat-stream', {method: 'POST', body: fd, credentials: 'same-origin', signal: controller.signal});
      if (r.redirected || r.status === 401) throw new Error('Your session ended. Sign in again.');
      if (!r.ok) throw new Error(r.status === 404 ? 'This conversation is unavailable. Start a new chat.' : 'Unable to send. Refresh and try again.');
      var reader = r.body.getReader(), decoder = new TextDecoder(), buffer = '';
      while (true) {
        var chunk = await reader.read();
        buffer += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
        var boundary;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          var packet = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
          if (packet.startsWith('data: ')) event(JSON.parse(packet.slice(6)));
        }
        if (chunk.done) break;
      }
      if (!gotResult) throw new Error('The connection ended before the answer arrived.');
    } catch (e) {
      waiting.remove(); failure(e.name === 'AbortError' ? 'Stopped waiting. An in-progress request may still finish; check saved conversations before retrying.' : e.message, question);
    } finally {
      clearTimeout(timeout); controller = null; busy = false; send.disabled = false; stop.hidden = true;
      document.getElementById('chat-clear').disabled = false; log.removeAttribute('aria-busy');
      if (!panel.hidden) input.focus({preventScroll: true}); save();
    }
  }
  fab.addEventListener('click', function () { panel.hidden ? show() : hide(); });
  document.getElementById('chat-close').addEventListener('click', hide);
  document.getElementById('chat-clear').addEventListener('click', newChat);
  document.getElementById('chat-history').addEventListener('click', listHistory);
  stop.addEventListener('click', function () { if (controller) controller.abort(); });
  useContext.addEventListener('change', refreshContext);
  document.querySelectorAll('.content form.filters').forEach(function (f) { f.addEventListener('input', refreshContext); });
  input.addEventListener('input', save);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(input.value); } });
  form.addEventListener('submit', function (e) { e.preventDefault(); ask(input.value); });
  document.addEventListener('keydown', function (e) {
    if (panel.hidden) return;
    if (e.key === 'Escape') hide();
    if (e.key === 'Tab' && !mount && mobile.matches) {
      var nodes = Array.from(panel.querySelectorAll('a[href],button,input,textarea,summary')).filter(function (el) { return !el.disabled && el.getClientRects().length; });
      var first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
    }
  });
  document.addEventListener('click', async function (e) {
    var askButton = e.target.closest('[data-chat-ask]');
    if (askButton) { e.preventDefault(); show(); ask(askButton.dataset.chatAsk); return; }
    var thread = e.target.closest('[data-chat-resume]');
    if (thread) { e.preventDefault(); resume(thread.dataset.chatResume); return; }
    if (e.target.closest('[data-chat-new]')) { newChat(); return; }
    var copy = e.target.closest('[data-chat-copy]');
    if (copy) {
      try { await navigator.clipboard.writeText(copy.closest('.msg, .explain-out').querySelector('.chat-answer-content').innerText); copy.textContent = 'Copied'; }
      catch (_) { copy.textContent = 'Select the answer to copy'; }
      return;
    }
    var citation = e.target.closest('#chat-log .cite');
    if (citation) { e.preventDefault(); var details = citation.closest('.msg').querySelector('.chat-sources'); if (details) { details.open = true; details.scrollIntoView({block: 'nearest'}); } }
  });
  (async function initialise() {
  hello(); await refreshContext(); syncPanel();
  try {
    var state = JSON.parse(sessionStorage.getItem(key) || 'null');
    if (state) { input.value = state.draft || ''; if (state.open && !mount) show(); if (state.parent) resume(state.parent, !!state.open || !!mount); }
  } catch (_) {}
  })();
})();
