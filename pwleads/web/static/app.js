// PowerLeads — light progressive enhancements (works fine without JS too).
(function () {
  // ---- Dark / light theme (persisted) ----
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("pw-theme", t); } catch (e) {}
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = t === "dark" ? "☀️" : "🌙";
  }
  document.addEventListener("DOMContentLoaded", function () {
    var saved = "light";
    try { saved = localStorage.getItem("pw-theme") || "light"; } catch (e) {}
    applyTheme(saved);
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme");
      applyTheme(cur === "dark" ? "light" : "dark");
    });

    // ---- Instant client-side search over the leads table ----
    var box = document.getElementById("lead-search");
    if (box) {
      box.addEventListener("input", function () {
        var q = box.value.toLowerCase();
        document.querySelectorAll("tbody tr[data-search]").forEach(function (tr) {
          tr.style.display = tr.getAttribute("data-search").indexOf(q) > -1 ? "" : "none";
        });
      });
    }

    // ---- Copy-to-clipboard buttons ----
    document.querySelectorAll("[data-copy]").forEach(function (b) {
      b.addEventListener("click", function () {
        var el = document.getElementById(b.getAttribute("data-copy"));
        if (!el) return;
        navigator.clipboard.writeText(el.value || el.textContent).then(function () {
          var t = b.textContent; b.textContent = "Copied!";
          setTimeout(function () { b.textContent = t; }, 1400);
        });
      });
    });

    // ---- Auto-dismiss toasts ----
    document.querySelectorAll(".toast").forEach(function (t) {
      setTimeout(function () {
        t.style.transition = "opacity .4s"; t.style.opacity = "0";
        setTimeout(function () { t.remove(); }, 400);
      }, 5000);
    });
  });

  // Apply saved theme ASAP to avoid a flash before DOMContentLoaded.
  try {
    var s = localStorage.getItem("pw-theme");
    if (s) document.documentElement.setAttribute("data-theme", s);
  } catch (e) {}
})();
