const track = document.getElementById("landingTrack");
const sections = [...document.querySelectorAll(".landing-section")];
const progress = [...document.querySelectorAll(".slide-progress button")];
const currentLabel = document.getElementById("slideCurrent");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let current = 0;
let animating = false;
let settleTimer = 0;
let lastWheel = 0;
let burstSpent = false;

function setCurrent(index) {
  current = Math.max(0, Math.min(sections.length - 1, index));
  document.querySelector(".landing")?.setAttribute("data-current", String(current));
  progress.forEach((button, i) => {
    button.classList.toggle("active", i === current);
    button.setAttribute("aria-current", i === current ? "step" : "false");
  });
  if (currentLabel) currentLabel.textContent = String(current + 1).padStart(2, "0");
}

function nearestSection() {
  const center = track.scrollTop + track.clientHeight * 0.5;
  return sections.reduce((closest, section, index) => {
    const distance = Math.abs(section.offsetTop + section.offsetHeight * 0.5 - center);
    return distance < closest.distance ? { index, distance } : closest;
  }, { index: current, distance: Infinity }).index;
}

function settle() {
  animating = false;
  setCurrent(nearestSection());
}

function armSettle() {
  window.clearTimeout(settleTimer);
  settleTimer = window.setTimeout(settle, 140);
}

function goTo(index) {
  setCurrent(index);
  const top = sections[current].offsetTop;
  if (Math.abs(track.scrollTop - top) < 2) return;
  animating = true;
  armSettle();
  track.scrollTo({ top, behavior: reducedMotion.matches ? "auto" : "smooth" });
}

// A section taller than the viewport scrolls natively until its edge is reached.
function roomWithin(direction) {
  const section = sections[current];
  if (direction > 0) return section.offsetTop + section.offsetHeight - (track.scrollTop + track.clientHeight) > 2;
  return track.scrollTop - section.offsetTop > 2;
}

progress.forEach((button) => button.addEventListener("click", () => goTo(Number(button.dataset.jump))));
document.addEventListener("keydown", (event) => {
  if (event.key !== " " && event.key !== "PageDown" && event.key !== "PageUp" && event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  if (event.target.matches("input, textarea, select, button, a")) return;
  event.preventDefault();
  const direction = event.key === "PageUp" || event.key === "ArrowUp" || (event.key === " " && event.shiftKey) ? -1 : 1;
  if (roomWithin(direction)) track.scrollBy({ top: direction * track.clientHeight * 0.8 });
  else goTo(current + direction);
});
track.addEventListener("wheel", (event) => {
  if (event.ctrlKey) return;
  const direction = Math.sign(event.deltaY);
  if (!direction || Math.abs(event.deltaY) < Math.abs(event.deltaX)) return;
  // Inertia keeps firing after the gesture; a burst moves at most one slide.
  const fresh = event.timeStamp - lastWheel > 160;
  lastWheel = event.timeStamp;
  if (fresh) burstSpent = false;
  if (!burstSpent && !animating && roomWithin(direction)) return;
  event.preventDefault();
  if (!fresh || animating) return;
  burstSpent = true;
  goTo(current + direction);
}, { passive: false });
track.addEventListener("scroll", () => {
  if (!animating) setCurrent(nearestSection());
  armSettle();
}, { passive: true });
setCurrent(nearestSection());
