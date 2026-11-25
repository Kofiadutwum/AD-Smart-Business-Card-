// Runs the slow sway on each [data-loop] picture while it is on screen. A click,
// tap or Enter on the picture pauses it for 3 seconds; reduced motion keeps it still.
(() => {
  const calm = matchMedia("(prefers-reduced-motion: reduce)");
  const RESUME_MS = 3000;

  document.querySelectorAll("[data-loop]").forEach((el) => {
    const img = el.querySelector("img");
    if (!img) return;
    const s = { seen: false, paused: false };
    let timer;

    const sync = () => el.classList.toggle("is-running", s.seen && !s.paused && !calm.matches);
    const toggle = () => {
      clearTimeout(timer);
      s.paused = !s.paused;
      if (s.paused) timer = setTimeout(() => { s.paused = false; sync(); }, RESUME_MS);
      sync();
    };

    img.tabIndex = 0; // keyboard users can reach the pause too
    img.addEventListener("click", toggle);
    img.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
    calm.addEventListener("change", sync);
    new IntersectionObserver(([entry]) => { s.seen = entry.isIntersecting; sync(); }).observe(el);
  });
})();
