(function () {
  var table = document.getElementById("sup-table");
  var search = document.getElementById("sup-search");
  var filterBar = document.getElementById("sup-filters");
  var emptyState = document.getElementById("sup-empty");
  var drawer = document.getElementById("supplier-drawer");
  var backdrop = document.getElementById("supplier-drawer-backdrop");
  var form = document.getElementById("supplier-drawer-form");

  if (!table || !drawer || !form) return;

  var activeFilter = "all";
  var selectedRow = null;
  var lastFocused = null;

  var drTitle = document.getElementById("dr-title");
  var drCode = document.getElementById("dr-code");
  var drBadge = document.getElementById("dr-badge");
  var drClues = document.getElementById("dr-clues");
  var drCluesText = document.getElementById("dr-clues-text");
  var drName = document.getElementById("dr-name");
  var drContact = document.getElementById("dr-contact");
  var drActive = document.getElementById("dr-active");
  var drProdsCount = document.getElementById("dr-prods-count");
  var drProdsSearch = document.getElementById("dr-prods-search");
  var drProds = document.getElementById("dr-prods");
  var drProdsEmpty = document.getElementById("dr-prods-empty");

  function applyFilters() {
    var q = (search ? search.value : "").trim().toLowerCase();
    var rows = table.querySelectorAll("tbody tr[data-id]");
    var visible = 0;

    rows.forEach(function (tr) {
      var isUnnamed = tr.dataset.unnamed === "1";
      var isLocal = tr.dataset.local === "1";
      var isActive = tr.dataset.active === "1";

      var ok = true;
      if (activeFilter === "unnamed" && !isUnnamed) ok = false;
      else if (activeFilter === "local" && !isLocal) ok = false;
      else if (activeFilter === "inactive" && isActive) ok = false;

      if (ok && q) {
        var hay = (tr.dataset.search || "").toLowerCase();
        if (hay.indexOf(q) === -1) ok = false;
      }

      tr.hidden = !ok;
      if (ok) visible++;
    });

    if (emptyState) emptyState.hidden = visible > 0;
  }

  if (filterBar) {
    filterBar.querySelectorAll("button[data-filter]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        filterBar.querySelectorAll("button[data-filter]").forEach(function (b) { b.classList.remove("on"); });
        btn.classList.add("on");
        activeFilter = btn.dataset.filter || "all";
        applyFilters();
      });
    });
  }

  if (search) {
    search.addEventListener("input", applyFilters);
  }

  function openDrawer(tr) {
    if (!tr) return;
    lastFocused = document.activeElement;
    if (selectedRow) selectedRow.classList.remove("is-selected");
    selectedRow = tr;
    selectedRow.classList.add("is-selected");

    var id = tr.dataset.id;
    form.action = "/suppliers/" + id;
    drTitle.textContent = tr.dataset.name || "";
    drCode.textContent = tr.dataset.meta || tr.dataset.code || "";

    if (drBadge) {
      if (tr.dataset.unnamed === "1") {
        drBadge.className = "badge b-warn";
        drBadge.textContent = tr.dataset.badgeUnnamed || "Unnamed";
        drBadge.hidden = false;
      } else if (tr.dataset.active === "0") {
        drBadge.className = "badge b-mute";
        drBadge.textContent = tr.dataset.badgeInactive || "Inactive";
        drBadge.hidden = false;
      } else {
        drBadge.className = "badge b-ok";
        drBadge.textContent = tr.dataset.badgeActive || "Active";
        drBadge.hidden = false;
      }
    }

    var clues = tr.dataset.clues || "";
    if (clues) {
      drCluesText.textContent = clues;
      drClues.hidden = false;
    } else {
      drClues.hidden = true;
    }

    drName.value = tr.dataset.name || "";
    drContact.value = tr.dataset.contact || "";
    drActive.checked = tr.dataset.active === "1";

    var items = [];
    tr.querySelectorAll("td.sup-products ul.sup-ul li").forEach(function (li) {
      items.push(li.textContent.trim());
    });
    drProdsCount.textContent = String(items.length);
    if (drProdsSearch) {
      drProdsSearch.value = "";
      drProdsSearch.hidden = items.length === 0;
    }
    drProds.innerHTML = "";
    if (items.length) {
      drProdsEmpty.hidden = true;
      items.forEach(function (pname) {
        var span = document.createElement("span");
        span.className = "sup-drawer-chip";
        span.textContent = pname;
        drProds.appendChild(span);
      });
    } else {
      drProdsEmpty.hidden = false;
    }

    backdrop.classList.add("open");
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    setTimeout(function () { drName.focus(); }, 40);
  }

  function closeDrawer() {
    backdrop.classList.remove("open");
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    if (selectedRow) {
      selectedRow.classList.remove("is-selected");
      selectedRow = null;
    }
    if (lastFocused && typeof lastFocused.focus === "function") {
      lastFocused.focus();
    }
  }

  table.querySelectorAll("tbody tr[data-id]").forEach(function (tr) {
    tr.addEventListener("click", function (e) {
      if (e.target.closest("a")) return;
      openDrawer(tr);
    });
    tr.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openDrawer(tr);
      }
    });
  });

  if (drProdsSearch) {
    drProdsSearch.addEventListener("input", function () {
      var q = drProdsSearch.value.trim().toLowerCase();
      var chips = drProds.querySelectorAll(".sup-drawer-chip");
      var shown = 0;
      chips.forEach(function (chip) {
        var match = !q || chip.textContent.toLowerCase().indexOf(q) !== -1;
        chip.hidden = !match;
        if (match) shown++;
      });
      drProdsEmpty.hidden = shown > 0;
    });
  }

  document.querySelectorAll("[data-drawer-close]").forEach(function (btn) {
    btn.addEventListener("click", closeDrawer);
  });
  backdrop.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && drawer.classList.contains("open")) {
      closeDrawer();
    }
  });
})();
