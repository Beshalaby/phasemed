// Dependency-free WebGL2 surface renderer for PatientObject meshes.
// One offscreen context renders every scene; callers blit MeshView.canvas into their 2D canvas,
// so overlays, picking and layout stay in the existing 2D code and share one camera.
(() => {
  const VERTEX = `#version 300 es
in vec3 aPos; in vec3 aNrm; uniform mat4 uMat; uniform mat3 uView; out vec3 vN;
void main() { vN = uView * aNrm; gl_Position = uMat * vec4(aPos, 1.0); }`;
  const FRAGMENT = `#version 300 es
precision mediump float; in vec3 vN; uniform vec3 uColor; uniform float uAlpha; out vec4 outColor;
void main() {
  vec3 n = normalize(vN); if (n.z < 0.0) n = -n; // voxel surfaces do not guarantee outward winding
  float key = max(dot(n, normalize(vec3(-0.45, 0.6, 0.66))), 0.0);
  float shade = 0.26 + 0.42 * n.z + 0.4 * key;
  float gloss = pow(max(dot(n, normalize(vec3(-0.45, 0.6, 1.66))), 0.0), 24.0) * 0.18;
  float rim = pow(1.0 - n.z, 3.0) * 0.2;
  outColor = vec4((uColor * shade + rim + gloss) * uAlpha, uAlpha);
}`;

  const canvas = document.createElement("canvas");
  const cache = new Map(); // `${modelId}:${objectId}` -> { state, vao, buffers, count, url, faces }
  const queue = [];
  let gl = null, program = null, uniforms = null, loading = false;

  function init() {
    gl = canvas.getContext("webgl2", { antialias: true, alpha: true, premultipliedAlpha: true });
    if (!gl) return false;
    const compile = (type, source) => { const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader); if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader)); return shader; };
    try {
      program = gl.createProgram(); gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX)); gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAGMENT));
      gl.bindAttribLocation(program, 0, "aPos"); gl.bindAttribLocation(program, 1, "aNrm"); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    } catch (error) { console.warn("MeshView disabled:", error.message); gl = null; return false; }
    uniforms = Object.fromEntries(["uMat", "uView", "uColor", "uAlpha"].map((name) => [name, gl.getUniformLocation(program, name)]));
    return true;
  }

  // Accepts the compiler's quad OBJ as well as triangles and v/vt/vn face syntax.
  function parseObj(text) {
    const positions = []; const indices = [];
    for (const line of text.split("\n")) {
      if (line.charCodeAt(0) === 118 && line.charCodeAt(1) === 32) { const parts = line.split(" "); positions.push(+parts[1], +parts[2], +parts[3]); }
      else if (line.charCodeAt(0) === 102 && line.charCodeAt(1) === 32) {
        const parts = line.trim().split(/\s+/); const count = positions.length / 3; const face = [];
        for (let i = 1; i < parts.length; i += 1) { const value = parseInt(parts[i], 10); if (value) face.push(value < 0 ? count + value : value - 1); }
        for (let i = 1; i + 1 < face.length; i += 1) indices.push(face[0], face[i], face[i + 1]);
      }
    }
    const pos = new Float32Array(positions); const idx = new Uint32Array(indices); const nrm = new Float32Array(pos.length);
    for (let t = 0; t < idx.length; t += 3) { // area-weighted vertex normals
      const a = idx[t] * 3, b = idx[t + 1] * 3, c = idx[t + 2] * 3;
      const ux = pos[b] - pos[a], uy = pos[b + 1] - pos[a + 1], uz = pos[b + 2] - pos[a + 2], vx = pos[c] - pos[a], vy = pos[c + 1] - pos[a + 1], vz = pos[c + 2] - pos[a + 2];
      const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      for (const v of [a, b, c]) { nrm[v] += nx; nrm[v + 1] += ny; nrm[v + 2] += nz; }
    }
    for (let v = 0; v < nrm.length; v += 3) { const length = Math.hypot(nrm[v], nrm[v + 1], nrm[v + 2]) || 1; nrm[v] /= length; nrm[v + 1] /= length; nrm[v + 2] /= length; }
    return { pos, nrm, idx };
  }

  function upload(entry, mesh) {
    const vao = gl.createVertexArray(); gl.bindVertexArray(vao); const buffers = [];
    [mesh.pos, mesh.nrm].forEach((data, location) => { const buffer = gl.createBuffer(); buffers.push(buffer); gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, 3, gl.FLOAT, false, 0, 0); });
    const elements = gl.createBuffer(); buffers.push(elements); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, elements); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.idx, gl.STATIC_DRAW); gl.bindVertexArray(null);
    Object.assign(entry, { state: "ready", vao, buffers, count: mesh.idx.length });
  }

  async function pump() {
    if (loading) return; loading = true;
    while (queue.length) {
      queue.sort((a, b) => a.faces - b.faces); const entry = queue.shift(); // small meshes first so the scene fills in progressively
      try { const response = await fetch(entry.url); if (!response.ok) throw new Error(String(response.status)); const mesh = parseObj(await response.text()); if (!mesh.idx.length) throw new Error("empty mesh"); if (gl && cache.get(entry.key) === entry) upload(entry, mesh); }
      catch { entry.state = "failed"; }
      api.onReady?.();
    }
    loading = false;
  }

  function release(entry) { if (!gl || entry.state !== "ready") return; gl.deleteVertexArray(entry.vao); entry.buffers.forEach((buffer) => gl.deleteBuffer(buffer)); }

  const api = {
    ok: false, canvas, onReady: null, parseObj,
    key: (modelId, objectId) => `${modelId}:${objectId}`,
    // Returns true once the object's mesh is on the GPU; starts loading it otherwise.
    ensure(modelId, object) {
      if (!api.ok || !object?.geometry?.mesh_id) return false;
      const key = api.key(modelId, object.id); let entry = cache.get(key);
      if (!entry) { entry = { key, state: "pending", faces: Number(object.metadata?.mesh_face_count) || 0, url: `/api/patient-models/${encodeURIComponent(modelId)}/objects/${encodeURIComponent(object.id)}/mesh` }; cache.set(key, entry); queue.push(entry); pump(); }
      return entry.state === "ready";
    },
    // Queues every mesh of a model so views that draw more objects than the current one (hologram) open without a load pause.
    warm(modelId, objects) { if (!modelId) return 0; let queued = 0; (objects || []).forEach((object) => { if (object?.type === "volume") return; const before = cache.size; api.ensure(modelId, object); if (cache.size !== before) queued += 1; }); return queued; },
    pending() { return queue.length + (loading ? 1 : 0); },
    ready(modelId, objectId) { return cache.get(api.key(modelId, objectId))?.state === "ready"; },
    setModel(modelIds) { const keep = modelIds.filter(Boolean); for (const [key, entry] of cache) if (!keep.some((id) => key.startsWith(`${id}:`))) { release(entry); cache.delete(key); } },
    // viewports: [{ x, y, w, h, mat, view }] in CSS px; items: [{ key, color: [r,g,b], alpha, depth }]
    draw({ width, height, dpr, viewports, items }) {
      if (!api.ok) return false;
      const W = Math.max(1, Math.round(width * dpr)), H = Math.max(1, Math.round(height * dpr)); if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
      const ready = items.filter((item) => cache.get(item.key)?.state === "ready");
      const solid = ready.filter((item) => item.alpha >= .99); const glass = ready.filter((item) => item.alpha < .99).sort((a, b) => a.depth - b.depth);
      gl.useProgram(program); gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.disable(gl.CULL_FACE); gl.enable(gl.SCISSOR_TEST); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.viewport(0, 0, W, H); gl.scissor(0, 0, W, H); gl.clearColor(0, 0, 0, 0); gl.depthMask(true); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      const paint = (item) => { const entry = cache.get(item.key); gl.uniform3fv(uniforms.uColor, item.color); gl.uniform1f(uniforms.uAlpha, item.alpha); gl.bindVertexArray(entry.vao); gl.drawElements(gl.TRIANGLES, entry.count, gl.UNSIGNED_INT, 0); };
      for (const port of viewports) {
        const x = Math.round(port.x * dpr), y = H - Math.round((port.y + port.h) * dpr), w = Math.round(port.w * dpr), h = Math.round(port.h * dpr);
        gl.viewport(x, y, w, h); gl.scissor(x, y, w, h); gl.uniformMatrix4fv(uniforms.uMat, false, port.mat); gl.uniformMatrix3fv(uniforms.uView, false, port.view);
        gl.disable(gl.BLEND); gl.depthMask(true); solid.forEach(paint);
        // Translucent meshes: drawing both sides blends a mesh's far side over its near side in buffer
        // order, which looks grainy. Back-face culling leaves one clean layer per mesh, and leaving depth
        // unwritten keeps faint shells from hiding what is behind them.
        gl.enable(gl.BLEND); gl.enable(gl.CULL_FACE); gl.cullFace(gl.BACK); gl.depthMask(false);
        glass.filter((item) => item.depth !== Infinity).forEach(paint);
        // The selection paints last over everything, with a depth pre-pass so concave shapes stay crisp.
        gl.disable(gl.CULL_FACE); gl.depthMask(true);
        glass.filter((item) => item.depth === Infinity).forEach((item) => {
          gl.clear(gl.DEPTH_BUFFER_BIT);
          gl.colorMask(false, false, false, false); paint(item);
          gl.colorMask(true, true, true, true); paint(item);
        });
      }
      gl.colorMask(true, true, true, true); gl.bindVertexArray(null);
      return true;
    },
  };

  canvas.addEventListener("webglcontextlost", (event) => { event.preventDefault(); api.ok = false; cache.clear(); queue.length = 0; });
  canvas.addEventListener("webglcontextrestored", () => { api.ok = init(); api.onReady?.(); });
  api.ok = init();
  window.MeshView = api;
})();
