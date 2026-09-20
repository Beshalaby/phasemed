// Landing scene: the patient model rendered as a radiograph-style volume, driven by scroll position.
// Geometry and every number shown come from the local API; with no compiled study (or no WebGL2)
// an illustrative procedural thorax is shown instead and the measured figures stay hidden.
(() => {
  const $ = (id) => document.getElementById(id);
  const canvas = $("scene"), overlay = $("sceneOverlay"), ink = overlay.getContext("2d");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const PHOSPHOR = [0.05, 0.43, 0.36], MARKER = [0.78, 0.45, 0.06], PRIOR = [0.28, 0.46, 0.41];
  const FOV = 26 * Math.PI / 180;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const dist3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  const number = (v) => Math.round(v).toLocaleString("en-US");

  // ---------- reveal + navigation chrome (independent of WebGL) ----------
  const revealer = new IntersectionObserver((entries) => entries.forEach((entry) => { if (entry.isIntersecting) entry.target.classList.add("in"); }), { threshold: 0.3 });
  document.querySelectorAll("[data-reveal]").forEach((node) => revealer.observe(node));
  const nav = $("nav"); const onScroll = () => nav.classList.toggle("stuck", scrollY > 24); addEventListener("scroll", onScroll, { passive: true }); onScroll();

  // ---------- WebGL ----------
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: false, powerPreference: "high-performance" });
  if (!gl) { canvas.remove(); overlay.remove(); return; }
  const VERT = `#version 300 es
in vec3 aPos; in vec3 aNrm; uniform mat4 uView; uniform mat4 uProj; uniform vec2 uShift; out vec3 vN; out float vZ;
void main() { vec4 v = uView * vec4(aPos, 1.0); vN = mat3(uView) * aNrm; vZ = aPos.z; vec4 c = uProj * v; c.xy += uShift * c.w; gl_Position = c; }`;
  const FRAG = `#version 300 es
precision highp float; in vec3 vN; in float vZ; uniform vec3 uColor; uniform float uGain; uniform float uFill; uniform float uSweep; uniform float uSweepOn; out vec4 o;
void main() {
  float facing = abs(normalize(vN).z);
  float rim = pow(1.0 - facing, 2.4);                 // silhouettes glow, faces stay clear: a radiograph, not a shaded solid
  float band = uSweepOn * exp(-pow((vZ - uSweep) / 3.2, 2.0));
  vec3 c = uColor * (0.55 + rim) * uGain + vec3(0.62, 0.88, 0.82) * band * 0.55;
  float opacity = clamp((uFill * 0.72 + rim * 0.5) * uGain + band * 0.25, 0.08, 0.7);
  o = vec4(c, opacity);
}`;
  const shader = (type, source) => { const s = gl.createShader(type); gl.shaderSource(s, source); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
  const program = gl.createProgram(); gl.attachShader(program, shader(gl.VERTEX_SHADER, VERT)); gl.attachShader(program, shader(gl.FRAGMENT_SHADER, FRAG));
  gl.bindAttribLocation(program, 0, "aPos"); gl.bindAttribLocation(program, 1, "aNrm"); gl.linkProgram(program);
  const U = Object.fromEntries(["uView", "uProj", "uShift", "uColor", "uGain", "uFill", "uSweep", "uSweepOn"].map((name) => [name, gl.getUniformLocation(program, name)]));

  function upload(mesh) {
    const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
    [mesh.pos, mesh.nrm].forEach((data, slot) => { gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer()); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); gl.enableVertexAttribArray(slot); gl.vertexAttribPointer(slot, 3, gl.FLOAT, false, 0, 0); });
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer()); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.idx, gl.STATIC_DRAW); gl.bindVertexArray(null);
    return { vao, count: mesh.idx.length };
  }
  function ellipsoid(centre, radii, rings = 22, segments = 30) {
    const pos = [], nrm = [], idx = [];
    for (let r = 0; r <= rings; r += 1) for (let s = 0; s <= segments; s += 1) {
      const phi = Math.PI * r / rings, theta = 2 * Math.PI * s / segments; const n = [Math.sin(phi) * Math.cos(theta), Math.sin(phi) * Math.sin(theta), Math.cos(phi)];
      pos.push(centre[0] + n[0] * radii[0], centre[1] + n[1] * radii[1], centre[2] + n[2] * radii[2]);
      const g = [n[0] / radii[0], n[1] / radii[1], n[2] / radii[2]]; const l = Math.hypot(...g); nrm.push(g[0] / l, g[1] / l, g[2] / l);
    }
    for (let r = 0; r < rings; r += 1) for (let s = 0; s < segments; s += 1) { const a = r * (segments + 1) + s, b = a + segments + 1; idx.push(a, b, a + 1, a + 1, b, b + 1); }
    return { pos: new Float32Array(pos), nrm: new Float32Array(nrm), idx: new Uint32Array(idx) };
  }

  // ---------- scene state ----------
  let objects = [];        // { label, kind: "anatomy" | "finding" | "prior", centroid, volume, colour, gain, target, mesh }
  let bounds = { centre: [0, 0, 150], radius: 190, zMin: 0, zMax: 300 };
  let live = null;         // { change: {...} } when real measurements are available
  let mode = "hero", preview = null, modeSince = 0, focusIndex = 0;
  const cam = { yaw: 0.5, pitch: 0.1, dist: 900, centre: [0, 0, 150], shift: 0.3 };
  const aim = { pitch: 0.1, dist: 900, centre: [0, 0, 150], shift: 0.3, lift: 0 };
  cam.lift = 0;
  const pointer = { x: 0, y: 0 };

  function setScene(list, newBounds) {
    objects = list.map((item) => ({ gain: 0, target: 0, ...item, mesh: upload(item.mesh) }));
    bounds = newBounds; cam.centre = [...bounds.centre]; aim.centre = [...bounds.centre]; cam.dist = aim.dist = fitDistance(1); applyMode(true);
  }
  const fitDistance = (scale) => bounds.radius / Math.tan(FOV / 2) * 1.12 / scale;
  const anatomy = () => objects.filter((item) => item.kind === "anatomy");
  const finding = () => objects.find((item) => item.kind === "finding");
  // Links shown in the "relate" view: the model's own computed relationships when live, nearest centroids otherwise.
  function relationsOf(hub) {
    if (live?.links?.length) return live.links.map((link) => ({ item: objects.find((item) => item.id === link.id), text: link.text })).filter((link) => link.item);
    return nearest(hub, 3).map(({ item }) => ({ item, text: null }));
  }
  const nearest = (from, count) => anatomy().filter((item) => item !== from).map((item) => ({ item, mm: dist3(item.centroid, from.centroid) })).sort((a, b) => a.mm - b.mm).slice(0, count);

  function applyMode(instant = false) {
    const active = preview || mode; const narrow = innerWidth < 900; const lesion = finding();
    aim.centre = [...bounds.centre]; aim.dist = fitDistance(1); aim.pitch = 0.1; aim.shift = narrow ? 0 : 0.24; aim.lift = narrow ? 0 : 0.02;
    if (active === "hero") { aim.dist = fitDistance(0.94); aim.pitch = 0.04; aim.shift = narrow ? 0 : 0.3; aim.lift = narrow ? 0 : 0.04; }
    if (active === "how") { aim.pitch = 0.18; aim.shift = narrow ? 0 : -0.28; aim.lift = narrow ? 0 : 0.13; }
    objects.forEach((item) => { item.target = item.kind === "prior" ? 0 : item.kind === "finding" ? 1.5 : 0.5; });
    if (active === "sweep") { aim.pitch = -0.08; aim.shift = narrow ? 0 : 0.28; aim.lift = narrow ? 0 : -0.1; objects.forEach((item) => { item.target = item.kind === "prior" ? 0 : 0.2; }); }
    if (active === "organs") { aim.dist = fitDistance(1.12); aim.pitch = 0.2; aim.shift = narrow ? 0 : -0.38; aim.lift = narrow ? 0 : -0.04; objects.forEach((item) => { item.target = item.kind === "prior" ? 0 : 0.1; }); }
    if (active === "relations") { aim.pitch = -0.05; aim.shift = narrow ? 0 : 0.18; aim.lift = narrow ? 0 : 0.16; const hub = lesion || anatomy()[0]; const near = hub ? relationsOf(hub).map((entry) => entry.item) : []; objects.forEach((item) => { item.target = item === hub ? 1.8 : near.includes(item) ? 0.62 : item.kind === "prior" ? 0 : 0.07; }); }
    if (active === "finding" && lesion) {
      const host = anatomy().slice().sort((a, b) => dist3(a.centroid, lesion.centroid) - dist3(b.centroid, lesion.centroid))[0];
      objects.forEach((item) => { item.target = item === lesion ? 1.25 : item.kind === "prior" ? 1.5 : item === host ? 0.3 : 0.05; });
      aim.centre = [...lesion.centroid]; aim.dist = fitDistance(3.1); aim.pitch = 0.16; aim.shift = narrow ? 0 : -0.3; aim.lift = narrow ? 0 : -0.1;
    }
    if (active === "holo") { aim.shift = 0; aim.lift = 0; aim.pitch = 0.14; }
    if (active === "surfaces") { aim.dist = fitDistance(1.12); aim.pitch = 0.08; aim.shift = narrow ? 0 : 0.38; aim.lift = narrow ? 0 : 0.06; }
    if (active === "rest") { objects.forEach((item) => { item.target = item.kind === "prior" ? 0 : item.kind === "finding" ? 0.7 : 0.22; }); aim.dist = fitDistance(1.08); aim.shift = narrow ? 0 : -0.3; aim.lift = narrow ? 0 : -0.02; }
    if (narrow) objects.forEach((item) => { item.target *= 0.72; });
    if (instant) { objects.forEach((item) => { item.gain = 0; }); }
    modeSince = performance.now();
  }
  function setMode(next) { if (next === mode) return; mode = next; focusIndex = 0; document.body.dataset.scene = next; applyMode(); }

  // ---------- camera maths (world is patient LPS mm; yaw 0 looks at the anterior surface) ----------
  function matrices(yaw, pitch, roll, aspect, centre, distance) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch), cr = Math.cos(roll), sr = Math.sin(roll);
    let R = [cy, -sy, 0], Up = [sy * sp, cy * sp, cp]; const T = [-sy * cp, -cy * cp, sp];
    [R, Up] = [[0, 1, 2].map((i) => R[i] * cr + Up[i] * sr), [0, 1, 2].map((i) => Up[i] * cr - R[i] * sr)];
    const t = [R, Up, T].map((row) => -(row[0] * centre[0] + row[1] * centre[1] + row[2] * centre[2]));
    const view = new Float32Array([R[0], Up[0], T[0], 0, R[1], Up[1], T[1], 0, R[2], Up[2], T[2], 0, t[0], t[1], t[2] - distance, 1]);
    const f = 1 / Math.tan(FOV / 2), near = Math.max(10, distance - bounds.radius * 3), far = distance + bounds.radius * 3;
    const proj = new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, 2 * far * near / (near - far), 0]);
    return { view, proj, basis: [R, Up, T], centre, distance, f, aspect };
  }
  function toScreen(point, m, width, height, shift, lift = 0) {
    const q = [point[0] - m.centre[0], point[1] - m.centre[1], point[2] - m.centre[2]]; const [R, Up, T] = m.basis;
    const x = R[0] * q[0] + R[1] * q[1] + R[2] * q[2], y = Up[0] * q[0] + Up[1] * q[1] + Up[2] * q[2], depth = m.distance - (T[0] * q[0] + T[1] * q[1] + T[2] * q[2]);
    return [(m.f / m.aspect * x / depth + shift + 1) * width / 2, (1 - m.f * y / depth - lift) * height / 2];
  }

  // ---------- drawing ----------
  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2), w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = overlay.width = w; canvas.height = overlay.height = h; }
    return { dpr, w, h };
  }
  function drawObjects(m, shift, lift, sweepOn, sweepZ) {
    gl.uniformMatrix4fv(U.uView, false, m.view); gl.uniformMatrix4fv(U.uProj, false, m.proj); gl.uniform2f(U.uShift, shift, lift); gl.uniform1f(U.uSweepOn, sweepOn); gl.uniform1f(U.uSweep, sweepZ);
    objects.forEach((item) => { if (item.gain < 0.004) return; gl.uniform3fv(U.uColor, item.colour); gl.uniform1f(U.uGain, item.gain); gl.uniform1f(U.uFill, item.kind === "anatomy" ? 0.035 : 0.3); gl.bindVertexArray(item.mesh.vao); gl.drawElements(gl.TRIANGLES, item.mesh.count, gl.UNSIGNED_INT, 0); });
  }
  function label(x, y, title, detail, dpr, align = "left", colour = "#08796b") {
    const titleFont = `500 ${11 * dpr}px "Plex Mono", monospace`, detailFont = `400 ${10.5 * dpr}px "Plex Mono", monospace`; const text = title.toUpperCase();
    ink.font = titleFont; let width = ink.measureText(text).width; if (detail) { ink.font = detailFont; width = Math.max(width, ink.measureText(detail).width); }
    const pad = 7 * dpr, left = align === "left" ? x : align === "right" ? x - width : x - width / 2;
    ink.fillStyle = "rgba(248, 251, 249, .94)"; ink.beginPath(); ink.roundRect(left - pad, y - 13 * dpr, width + pad * 2, (detail ? 36 : 20) * dpr, 4 * dpr); ink.fill();
    ink.textAlign = align; ink.fillStyle = colour; ink.font = titleFont; ink.fillText(text, x, y);
    if (detail) { ink.fillStyle = "rgba(23, 34, 31, .78)"; ink.font = detailFont; ink.fillText(detail, x, y + 16 * dpr); }
  }
  function leader(from, to, dpr, colour = "rgba(8, 121, 107, .58)") { ink.strokeStyle = colour; ink.lineWidth = dpr; ink.beginPath(); ink.moveTo(from[0], from[1]); ink.lineTo(to[0], to[1]); ink.stroke(); ink.fillStyle = colour; ink.beginPath(); ink.arc(from[0], from[1], 2.4 * dpr, 0, Math.PI * 2); ink.fill(); }

  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now; const { dpr, w, h } = resize(); const active = preview || mode; const narrow = innerWidth < 900;
    if (!reduced.matches) cam.yaw += dt * (active === "finding" ? 0.1 : 0.16);
    const k = 1 - Math.exp(-dt * 3.2); cam.pitch += (aim.pitch - cam.pitch) * k; cam.dist += (aim.dist - cam.dist) * k; cam.shift += (aim.shift - cam.shift) * k; cam.lift += (aim.lift - cam.lift) * k;
    for (let i = 0; i < 3; i += 1) cam.centre[i] += (aim.centre[i] - cam.centre[i]) * k;
    objects.forEach((item) => { item.gain += (item.target - item.gain) * (1 - Math.exp(-dt * 4.5)); });

    if (active === "organs") { const list = anatomy(); if (list.length) { focusIndex = Math.floor((now - modeSince) / 1900) % list.length; list.forEach((item, i) => { item.target = (i === focusIndex ? 1.35 : 0.1) * (narrow ? 0.72 : 1); }); } }
    const span = bounds.zMax - bounds.zMin; const phase = ((now - modeSince) / 4200) % 1; const sweepZ = bounds.zMin + span * (0.5 - 0.5 * Math.cos(phase * Math.PI * 2));

    gl.viewport(0, 0, w, h); gl.clearColor(0.9569, 0.9686, 0.9608, 1); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(program); gl.disable(gl.DEPTH_TEST); gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    ink.clearRect(0, 0, w, h);
    const yaw = cam.yaw + pointer.x * 0.22, pitch = cam.pitch - pointer.y * 0.1;

    if (active === "holo") {
      // Four-direction Pepper's Ghost layout: the same model seen from four sides, each rotated toward the pyramid.
      const side = Math.min(w * (narrow ? 0.9 : 0.4), h * 0.74), cell = side / 3, cx = narrow ? w / 2 : w * 0.755, cy = h / 2;
      [[0, -1], [1, 0], [0, 1], [-1, 0]].forEach(([dx, dy], i) => {
        const x = cx + dx * cell - cell / 2, y = cy + dy * cell - cell / 2; gl.viewport(Math.round(x), Math.round(h - y - cell), Math.round(cell), Math.round(cell));
        drawObjects(matrices(yaw + i * Math.PI / 2, pitch, i * Math.PI / 2, 1, cam.centre, fitDistance(0.92)), 0, 0, 0, 0);
      });
      ink.strokeStyle = "rgba(8, 121, 107, .45)"; ink.lineWidth = dpr; ink.save(); ink.translate(cx, cy); ink.rotate(Math.PI / 4); ink.strokeRect(-cell * 0.11, -cell * 0.11, cell * 0.22, cell * 0.22); ink.restore();
      if (!narrow) label(cx, cy + cell * 1.62, "Hologram", "four views · hold space to speak", dpr, "center");
    } else {
      const m = matrices(yaw, pitch, 0, w / h, cam.centre, cam.dist); drawObjects(m, cam.shift, cam.lift, active === "sweep" ? 1 : 0, sweepZ);
      if (!narrow && objects.length) annotate(active, m, w, h, dpr, sweepZ);
    }
    requestAnimationFrame(frame);
  }

  function annotate(active, m, w, h, dpr, sweepZ) {
    const at = (point) => toScreen(point, m, w, h, cam.shift, cam.lift); const settle = clamp((performance.now() - modeSince - 350) / 500, 0, 1); ink.globalAlpha = settle;
    if (active === "sweep") { const p = at([bounds.centre[0], bounds.centre[1], sweepZ]); const x = Math.min(p[0] + bounds.radius * 0.72 * (h / 2) * m.f / m.distance, w - 250 * dpr); leader([x - 26 * dpr, p[1]], [x + 30 * dpr, p[1]], dpr); label(x + 40 * dpr, p[1] + 4 * dpr, "Axial", live ? `z ${number(sweepZ - bounds.zMin)} mm` : "source plane", dpr); }
    if (active === "organs") { const item = anatomy()[focusIndex]; if (item) { const p = at(item.centroid); const tip = [p[0] + 120 * dpr, p[1] - 70 * dpr]; leader(p, tip, dpr); label(tip[0] + 10 * dpr, tip[1] + 3 * dpr, item.label, item.volume ? `${number(item.volume / 1000)} mL · object ${focusIndex + 1} / ${anatomy().length}` : "compiled object", dpr); } }
    if (active === "relations") { const hub = finding() || anatomy()[0]; if (hub) {
      const from = at(hub.centroid), middle = at(bounds.centre); const column = Math.min(middle[0] + bounds.radius * m.f / m.distance * (h / 2) * 0.7, w - 250 * dpr);
      const tags = relationsOf(hub).map(({ item, text }) => ({ to: at(item.centroid), text, name: item.label })).sort((a, b) => a.to[1] - b.to[1]); const gap = 58 * dpr, top = from[1] - (tags.length - 1) * gap / 2;
      tags.forEach((tag, i) => { const y = top + i * gap; leader(from, tag.to, dpr, "rgba(184, 117, 19, .56)"); ink.strokeStyle = "rgba(8, 121, 107, .45)"; ink.lineWidth = dpr; ink.beginPath(); ink.moveTo(tag.to[0], tag.to[1]); ink.lineTo(column - 14 * dpr, y - 4 * dpr); ink.lineTo(column - 4 * dpr, y - 4 * dpr); ink.stroke(); label(column + 6 * dpr, y, tag.name, tag.text, dpr); });
    } }
    if (active === "finding") { const lesion = finding(); if (lesion) { const p = at(lesion.centroid); const tip = [p[0] + 130 * dpr, p[1] - 84 * dpr]; leader(p, tip, dpr, "rgba(184, 117, 19, .72)"); label(tip[0] + 10 * dpr, tip[1] + 3 * dpr, lesion.label, live?.change ? `${number(live.change.to)} mm³ · prior shown in teal` : lesion.volume ? `${number(lesion.volume)} mm³` : "finding", dpr, "left", "#b87513"); } }
    ink.globalAlpha = 1;
  }

  // ---------- scroll + hover drive the scene ----------
  const watcher = new IntersectionObserver((entries) => { entries.forEach((entry) => { if (entry.isIntersecting) setMode(entry.target.dataset.scene); }); }, { rootMargin: "-45% 0px -45% 0px" });
  document.querySelectorAll("[data-scene]").forEach((node) => watcher.observe(node));
  document.querySelectorAll("#surfaceList li").forEach((row) => {
    const on = () => { document.querySelectorAll("#surfaceList li").forEach((other) => other.classList.toggle("on", other === row)); preview = row.dataset.preview; focusIndex = 0; applyMode(); };
    const off = () => { row.classList.remove("on"); if (preview === row.dataset.preview) { preview = null; applyMode(); } };
    row.addEventListener("pointerenter", on); row.addEventListener("focus", on); row.addEventListener("pointerleave", off); row.addEventListener("blur", off);
  });
  addEventListener("pointermove", (event) => { pointer.x = event.clientX / innerWidth - 0.5; pointer.y = event.clientY / innerHeight - 0.5; }, { passive: true });
  addEventListener("resize", () => applyMode());
  document.body.dataset.scene = mode;

  // ---------- scenes ----------
  function illustrative() {
    const parts = [["Right lung", [-62, -8, 150], [46, 62, 110]], ["Left lung", [62, -8, 150], [44, 62, 110]], ["Heart", [16, -34, 120], [36, 30, 45]], ["Thoracic aorta", [20, 28, 150], [11, 11, 130]], ["Trachea", [0, -30, 245], [8, 8, 45]], ["Thoracic spine", [0, 58, 150], [17, 15, 148]]];
    const list = parts.map(([name, centre, radii]) => ({ label: name, kind: "anatomy", centroid: centre, colour: PHOSPHOR, mesh: ellipsoid(centre, radii) }));
    list.push({ label: "Finding", kind: "finding", centroid: [-66, -16, 205], colour: MARKER, mesh: ellipsoid([-66, -16, 205], [9, 9, 9], 12, 16) });
    setScene(list, { centre: [0, 5, 150], radius: 185, zMin: 0, zMax: 300 });
  }

  async function loadLive() {
    const json = async (url) => { const response = await fetch(url); if (!response.ok) throw new Error(`${response.status} ${url}`); return response.json(); };
    const studies = (await json("/api/studies")).filter((study) => study.model_id); if (!studies.length) return;
    const models = await Promise.all(studies.map((study) => json(`/api/patient-models/${encodeURIComponent(study.model_id)}`).catch(() => null)));
    const index = Math.max(0, models.findIndex((model) => model?.metadata?.prior_model_id)); const model = models[index], study = studies[index]; if (!model) return;
    const wanted = model.objects.filter((item) => (item.type === "anatomy" || item.type === "finding") && item.geometry?.mesh_id); if (!wanted.length) return;
    const mesh = async (modelId, item) => { const response = await fetch(`/api/patient-models/${encodeURIComponent(modelId)}/objects/${encodeURIComponent(item.id)}/mesh`); if (!response.ok) throw new Error("mesh"); return MeshView.parseObj(await response.text()); };
    const list = (await Promise.all(wanted.map(async (item) => { try { return { label: item.label, kind: item.type, centroid: item.geometry.centroid, volume: item.geometry.volume_mm3, colour: item.type === "finding" ? MARKER : PHOSPHOR, mesh: await mesh(model.id, item), id: item.id }; } catch { return null; } }))).filter(Boolean);
    if (!list.some((item) => item.kind === "anatomy")) return;

    // Measured change: the finding's temporal link to the prior study, if one exists.
    let change = null; const lesion = list.find((item) => item.kind === "finding"); const link = lesion && (model.temporal_links || []).find((entry) => entry.source_object_id === lesion.id && entry.type === "changed_from");
    if (link && model.metadata?.prior_model_id) { try {
      const prior = await json(`/api/patient-models/${encodeURIComponent(model.metadata.prior_model_id)}`); const before = prior.objects.find((item) => item.id === link.target_object_id); const priorStudy = studies.find((entry) => entry.id === prior.study_id);
      if (before?.geometry) { list.push({ label: "Prior", kind: "prior", centroid: before.geometry.centroid, colour: PRIOR, mesh: await mesh(prior.id, before) }); change = { percent: link.changes?.volume_change_percent, from: before.geometry.volume_mm3, to: lesion.volume, months: monthsBetween(priorStudy?.study_date, study.study_date), label: lesion.label }; }
    } catch { change = null; } }

    const boxes = wanted.map((item) => item.geometry.bounding_box); const low = [0, 1, 2].map((i) => Math.min(...boxes.map((box) => box.min[i]))), high = [0, 1, 2].map((i) => Math.max(...boxes.map((box) => box.max[i])));
    const links = []; if (lesion) { const byId = new Map(list.map((item) => [item.id, item])); (model.relationships || []).forEach((rel) => {
      if (rel.source_object_id !== lesion.id || !byId.has(rel.target_object_id) || links.some((link) => link.id === rel.target_object_id)) return;
      if (rel.type === "inside") links.push({ id: rel.target_object_id, text: "contains it", rank: -1 }); else if (rel.type === "near" && Number.isFinite(rel.value)) links.push({ id: rel.target_object_id, text: `${number(rel.value)} mm away`, rank: rel.value });
    }); links.sort((a, b) => a.rank - b.rank); links.length = Math.min(links.length, 3); }
    live = { change, links }; setScene(list, { centre: low.map((v, i) => (v + high[i]) / 2), radius: dist3(low, high) / 2, zMin: low[2], zMax: high[2] });
    if (change && Number.isFinite(change.percent)) { $("statDelta").textContent = `${change.percent > 0 ? "+" : ""}${number(change.percent)}%`; $("statFrom").textContent = number(change.from); $("statTo").textContent = number(change.to); $("statSpan").textContent = change.months ? `${change.months} months` : "prior study"; $("statObject").textContent = change.label; $("changeStat").hidden = false; }
  }
  function monthsBetween(a, b) { if (!/^\d{8}$/.test(a || "") || !/^\d{8}$/.test(b || "")) return null; const months = (Number(b.slice(0, 4)) - Number(a.slice(0, 4))) * 12 + Number(b.slice(4, 6)) - Number(a.slice(4, 6)); return months > 0 ? months : null; }

  illustrative();
  requestAnimationFrame(frame);
  loadLive().catch(() => {});
})();
