// Interface colours: 跟随系统 / 深色 / 淡色. The first visit opens in 淡色 (user, 2026-10-26).
// Loaded in <head> so the page never flashes the other theme; the choice is kept in this
// browser only. Sets <html data-theme="dark|light"> and wires the buttons in #themeSeg.
(function () {
  "use strict";
  const KEY = "darkslide.theme";
  const CHOICES = ["system", "dark", "light"];
  const root = document.documentElement;
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function saved() {
    try {
      const value = localStorage.getItem(KEY);
      return CHOICES.includes(value) ? value : "light";
    } catch (e) { return "light"; }  // storage blocked: the default
  }

  function apply(choice) {
    const dark = choice === "dark" || (choice === "system" && !!media && media.matches);
    root.dataset.theme = dark ? "dark" : "light";
    document.querySelectorAll("#themeSeg button").forEach((b) => {
      const on = b.dataset.theme === choice;
      b.classList.toggle("on", on);
      b.setAttribute("aria-checked", String(on));
    });
  }

  function set(choice) {
    if (!CHOICES.includes(choice)) return;
    try { localStorage.setItem(KEY, choice); } catch (e) { /* not remembered, still applied */ }
    apply(choice);
  }

  if (media) media.addEventListener("change", () => { if (saved() === "system") apply("system"); });
  apply(saved());
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("#themeSeg button").forEach((b) => b.addEventListener("click", () => set(b.dataset.theme)));
    apply(saved());
  });
  window.DarkslideTheme = { get: saved, set };
})();
