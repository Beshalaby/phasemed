const track = document.getElementById("landingTrack");
const sections = [...document.querySelectorAll(".landing-section")];
const progress = [...document.querySelectorAll(".slide-progress button")];
const currentLabel = document.getElementById("slideCurrent");
let current = 0;
let wheelLock = false;

function goTo(index, behavior = "smooth") {
  current = Math.max(0, Math.min(sections.length - 1, index));
  sections[current].scrollIntoView({ behavior, block: "start" });
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

const observer = new IntersectionObserver((entries) => {
  const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
  if (visible) goTo(Number(visible.target.dataset.slide), "auto");
}, { root: track, threshold: [0.6, 0.8] });
sections.forEach((section) => observer.observe(section));

progress.forEach((button) => button.addEventListener("click", () => goTo(Number(button.dataset.jump))));
document.addEventListener("keydown", (event) => {
  if (event.key !== " " && event.key !== "PageDown" && event.key !== "PageUp" && event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  if (event.target.matches("input, textarea, select, button, a")) return;
  event.preventDefault();
  const direction = event.key === "PageUp" || event.key === "ArrowUp" ? -1 : 1;
  goTo(current + direction);
});
track.addEventListener("wheel", (event) => {
  if (Math.abs(event.deltaY) < 8 || wheelLock) return;
  event.preventDefault();
  wheelLock = true;
  goTo(current + (event.deltaY > 0 ? 1 : -1));
  window.setTimeout(() => { wheelLock = false; }, 650);
}, { passive: false });
track.addEventListener("scrollend", () => goTo(nearestSection(), "auto"));
goTo(0, "auto");
