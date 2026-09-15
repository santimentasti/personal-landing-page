(function () {
  "use strict";

  // Mobile nav toggle
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.setAttribute("aria-label", open ? "Cerrar menú" : "Abrir menú");
    });
    nav.addEventListener("click", function (e) {
      if (e.target.tagName === "A" && nav.classList.contains("open")) {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  // Current year in footer
  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());

  // Reveal on scroll (no-op when reduced motion or no IntersectionObserver)
  var items = document.querySelectorAll(".reveal");
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!("IntersectionObserver" in window) || reduced) {
    items.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("in");
          io.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.1 });
    items.forEach(function (el) { io.observe(el); });
  }

  // Blog teaser: latest 3 posts from blog/posts.json (section stays hidden on failure)
  var teaser = document.getElementById("blog-teaser");
  var list = document.getElementById("blog-teaser-list");
  if (teaser && list && window.fetch) {
    fetch("blog/posts.json", { cache: "no-cache" })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (posts) {
        if (!Array.isArray(posts) || posts.length === 0) return;
        posts.slice(0, 3).forEach(function (p) {
          var card = document.createElement("article");
          card.className = "post-card";
          var time = document.createElement("time");
          time.dateTime = p.date;
          time.textContent = formatDate(p.date);
          var h3 = document.createElement("h3");
          var a = document.createElement("a");
          a.href = "blog/" + p.path;
          a.textContent = p.title;
          h3.appendChild(a);
          var desc = document.createElement("p");
          desc.textContent = p.summary;
          var more = document.createElement("a");
          more.className = "text-link";
          more.href = "blog/" + p.path;
          more.textContent = "Leer";
          card.appendChild(time);
          card.appendChild(h3);
          card.appendChild(desc);
          card.appendChild(more);
          list.appendChild(card);
        });
        teaser.hidden = false;
      })
      .catch(function () { /* keep hidden */ });
  }

  function formatDate(iso) {
    var parts = String(iso).split("-");
    if (parts.length !== 3) return iso;
    var months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
    return parseInt(parts[2], 10) + " " + months[parseInt(parts[1], 10) - 1] + " " + parts[0];
  }
})();
