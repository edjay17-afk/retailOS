(function () {
  'use strict';
  var u = document.getElementById('login-username'), pw = document.getElementById('login-password'), eye = document.getElementById('login-eye'),
      caps = document.getElementById('login-caps'), rem = document.getElementById('login-remember'), KEY = 'ros-login-name';
  if (eye && pw) eye.addEventListener('click', function () {
    var show = pw.type === 'password';
    pw.type = show ? 'text' : 'password';
    eye.textContent = show ? eye.dataset.hide : eye.dataset.show;
    eye.setAttribute('aria-pressed', show ? 'true' : 'false');
  });
  if (pw && caps) {
    var check = function (e) { if (e.getModifierState) caps.hidden = !e.getModifierState('CapsLock'); };
    pw.addEventListener('keydown', check); pw.addEventListener('keyup', check); pw.addEventListener('blur', function () { caps.hidden = true; });
  }
  if (u && rem) {
    try { var saved = localStorage.getItem(KEY); if (saved && !u.value) { u.value = saved; rem.checked = true; if (pw) pw.focus(); } } catch (e) { /* storage blocked */ }
    if (u.form) u.form.addEventListener('submit', function () {
      try { if (rem.checked) localStorage.setItem(KEY, u.value.trim()); else localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
    });
  }
})();
