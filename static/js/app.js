/* Shared helpers: icons, mobile nav, toast. */
(function () {
  "use strict";

  window.POPROX = window.POPROX || {};

  POPROX.icons = function () {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
    }
  };

  var toastTimer = null;
  POPROX.toast = function (message, kind) {
    var el = document.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.className = "toast" + (kind === "error" ? " toast-error" : "");
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, kind === "error" ? 6000 : 2200);
  };

  // Go to another page of the site. The Streamlit wrapper (streamlit_app.py) replaces
  // this with its own version; in the normal Flask site it is a plain page load.
  if (typeof POPROX.navigate !== "function") {
    POPROX.navigate = function (url) { window.location.href = url; };
  }

  POPROX.csrf = function () {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  };

  document.addEventListener("DOMContentLoaded", function () {
    POPROX.icons();
    var toggle = document.querySelector(".nav-toggle");
    var nav = document.getElementById("main-nav");
    if (toggle && nav) {
      toggle.addEventListener("click", function () {
        var open = nav.classList.toggle("open");
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }
  });
})();
