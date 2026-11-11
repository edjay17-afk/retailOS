/* Ops Console — runtime DOM restructure for the redesign overlay.
   Reads the existing sidebar nav and rebuilds it as a slim icon rail (desktop)
   or a bottom tab bar (mobile). Never submits forms or navigates on its own. */
(function () {
  'use strict';

  var MORE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>';
  var HOME_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/></svg>';

  function svgOf(el, fallback) {
    if (el && el.tagName && el.tagName.toLowerCase() === 'svg') return el.outerHTML;
    var s = el && el.querySelector('svg');
    if (s) return s.outerHTML;
    return fallback || MORE_SVG;
  }

  function readNav() {
    var groups = [];
    document.querySelectorAll('#sidebar-nav .nav-group').forEach(function (g) {
      var isHome = g.classList.contains('nav-home');
      var head = g.querySelector('.ng');
      var label = isHome ? 'Today' : ((head && head.querySelector('.group-label') || {}).textContent || g.getAttribute('data-group') || '').trim();
      var icon = svgOf(isHome ? g.querySelector('a.nl') : (head && head.querySelector('.group-icon')), HOME_SVG);
      var links = [];
      g.querySelectorAll('a.nl').forEach(function (a) {
        var lab = (a.querySelector('.nav-label') || {}).textContent || a.textContent;
        var tag = a.querySelector('.tag');
        links.push({ href: a.getAttribute('href'), label: lab.trim(), on: a.classList.contains('on'), tag: tag ? tag.textContent.trim() : '' });
      });
      var href = isHome && links[0] ? links[0].href : (links[0] && links[0].href);
      groups.push({ label: label, icon: icon, links: links, active: g.classList.contains('current-group') || links.some(function (l) { return l.on; }), isHome: isHome, href: href });
    });
    return groups;
  }

  function buildRail(groups) {
    var rail = document.createElement('div');
    rail.className = 'oc-rail';

    var logo = document.createElement('a');
    logo.className = 'oc-logo';
    logo.href = '/';
    var mark = document.querySelector('#sidebar .brand .mark img, .brand .mark img');
    logo.innerHTML = mark ? mark.outerHTML : '';
    rail.appendChild(logo);

    var nav = document.createElement('nav');
    groups.forEach(function (g) {
      var item = document.createElement('div');
      item.className = 'oc-item' + (g.active ? ' active' : '');
      item.setAttribute('role', 'button');
      item.setAttribute('aria-label', g.label);
      item.innerHTML = g.icon;
      if (g.isHome) {
        item.addEventListener('click', function () { location.assign(g.href || '/'); });
      } else {
        var fly = document.createElement('div');
        fly.className = 'oc-fly';
        fly.innerHTML = '<div class="oc-fly-t"></div>';
        fly.querySelector('.oc-fly-t').textContent = g.label;
        g.links.forEach(function (l) {
          var a = document.createElement('a');
          a.href = l.href;
          a.className = l.on ? 'on' : '';
          a.textContent = l.label;
          if (l.tag) { var t = document.createElement('span'); t.className = 'tag'; t.textContent = l.tag; a.appendChild(t); }
          fly.appendChild(a);
        });
        item.appendChild(fly);
        item.addEventListener('click', function (e) {
          if (e.target.closest('a')) return;
          document.querySelectorAll('.oc-item.pinned').forEach(function (o) { if (o !== item) o.classList.remove('pinned'); });
          item.classList.toggle('pinned');
        });
      }
      nav.appendChild(item);
    });
    rail.appendChild(nav);

    var spacer = document.createElement('div');
    spacer.className = 'oc-spacer';
    rail.appendChild(spacer);

    var bot = document.createElement('div');
    bot.className = 'oc-rbot';
    var themeBtn = document.querySelector('.header-actions .iconbtn:not(.menu-btn):not(:has(b))') ||
      document.querySelector('.topbar .iconbtn:not(.menu-btn)');
    if (themeBtn) {
      var t = document.createElement('div');
      t.className = 'oc-item';
      t.innerHTML = svgOf(themeBtn);
      t.title = 'Theme';
      bot.appendChild(t);
    }
    var langOn = document.querySelector('.sidebar-language a.on, .lang-toggle a.on');
    var lang = document.createElement('div');
    lang.className = 'oc-lang';
    lang.textContent = langOn ? (langOn.textContent.trim().slice(0, 2).toUpperCase() || 'EN') : 'EN';
    bot.appendChild(lang);
    var av = document.querySelector('.header-account .avatar, .topbar .avatar, .avatar');
    var avd = document.createElement('div');
    avd.className = 'oc-avatar';
    avd.textContent = av ? av.textContent.trim().slice(0, 2) : 'U';
    bot.appendChild(avd);
    rail.appendChild(bot);

    return rail;
  }

  function buildPanel(group) {
    var panel = document.createElement('div');
    panel.className = 'oc-panel';
    var t = document.createElement('div');
    t.className = 'oc-panel-t';
    t.textContent = group.label;
    panel.appendChild(t);
    group.links.forEach(function (l) {
      var a = document.createElement('a');
      a.href = l.href;
      a.className = l.on ? 'on' : '';
      a.textContent = l.label;
      if (l.tag) { var tag = document.createElement('span'); tag.className = 'tag'; tag.textContent = l.tag; a.appendChild(tag); }
      panel.appendChild(a);
    });
    return panel;
  }

  function moveSearch() {
    var s = document.querySelector('#sidebar .sidebar-search, .sidebar-search');
    var tb = document.querySelector('.topbar');
    if (s && tb) {
      var anchor = tb.querySelector('.grow') || tb.children[1];
      tb.insertBefore(s, anchor || null);
    }
  }

  function buildTabs(groups) {
    var tabs = document.createElement('nav');
    tabs.className = 'oc-tabs';
    function findGroup(re) {
      return groups.find(function (g) { return re.test(g.label); }) || groups.find(function (g) { return g.links.some(function (l) { return re.test(l.href); }); });
    }
    var defs = [
      { label: 'Today', group: findGroup(/^Today$/i) || groups[0], href: '/' },
      { label: 'Stock', group: findGroup(/inventory/i), href: '/inventory' },
      { label: 'Replenish', group: findGroup(/replenishment/i), href: '/replenishment' },
    ];
    defs.forEach(function (d) {
      var a = document.createElement('a');
      a.href = d.href;
      var on = d.group && d.group.active;
      a.className = on ? 'on' : '';
      a.innerHTML = (d.group ? d.group.icon : HOME_SVG) + '<span></span>';
      a.querySelector('span').textContent = d.label;
      tabs.appendChild(a);
    });
    var more = document.createElement('a');
    more.href = '#';
    more.innerHTML = MORE_SVG + '<span>More</span>';
    more.addEventListener('click', function (e) {
      e.preventDefault();
      var sheet = document.querySelector('.oc-more');
      if (sheet) sheet.classList.toggle('open');
    });
    tabs.appendChild(more);
    document.body.appendChild(tabs);

    var sheet = document.createElement('div');
    sheet.className = 'oc-more';
    groups.forEach(function (g) {
      var t = document.createElement('div');
      t.className = 'oc-fly-t';
      t.textContent = g.label;
      sheet.appendChild(t);
      g.links.forEach(function (l) {
        var a = document.createElement('a');
        a.href = l.href;
        a.className = l.on ? 'on' : '';
        a.textContent = l.label;
        sheet.appendChild(a);
      });
    });
    document.body.appendChild(sheet);
  }

  function buildTopnav(groups) {
    var bar = document.createElement('nav');
    bar.className = 'oc-topnav';
    var mark = document.querySelector('#sidebar .brand .mark img, .brand .mark img');
    var brand = document.createElement('a');
    brand.href = '/';
    brand.className = 'oc-tn-brand';
    brand.innerHTML = (mark ? mark.outerHTML : '') + '<span>Retail OS</span>';
    bar.appendChild(brand);
    groups.forEach(function (g) {
      var wrap = document.createElement('div');
      wrap.className = 'oc-tn-item' + (g.active ? ' active' : '');
      var btn = document.createElement('a');
      btn.href = g.isHome ? (g.href || '/') : (g.links[0] ? g.links[0].href : '#');
      btn.textContent = g.label;
      wrap.appendChild(btn);
      if (!g.isHome && g.links.length) {
        var drop = document.createElement('div');
        drop.className = 'oc-tn-drop';
        g.links.forEach(function (l) {
          var a = document.createElement('a');
          a.href = l.href;
          a.className = l.on ? 'on' : '';
          a.textContent = l.label;
          drop.appendChild(a);
        });
        wrap.appendChild(drop);
      }
      bar.appendChild(wrap);
    });
    var main = document.querySelector('.main');
    if (main) main.insertBefore(bar, main.firstChild);
  }

  window.OC = {
    build: function (mode) {
      document.documentElement.classList.add('oc');
      if (mode === 'topnav') document.documentElement.classList.add('oc-topnav-mode');
      var groups = readNav();
      if (!groups.length) return false;
      if (mode === 'topnav') {
        buildTopnav(groups);
      } else {
        var app = document.querySelector('.app');
        if (app) {
          app.insertBefore(buildRail(groups), app.firstChild);
          var active = groups.find(function (g) { return g.active && !g.isHome; });
          if (active) {
            document.documentElement.classList.add('oc-has-panel');
            app.insertBefore(buildPanel(active), app.children[1]);
          }
        }
        buildTabs(groups);
      }
      moveSearch();
      return true;
    }
  };
})();
