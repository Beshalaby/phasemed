const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const number = (value, digits = 1) => Number(value ?? 0).toLocaleString(undefined, { maximumFractionDigits: digits });
const typeColor = (object) => object?.type === "finding" || object?.type === "lesion" ? "#efc77e" : object?.type === "device" ? "#c7b2ef" : object?.type === "region" ? "#98d6ca" : "#70b8b0";

const state = {
  studies: [], study: null, viewerStudy: null, model: null, priorModel: null, priorStudy: null, viewModel: null, selectedId: null, mode: "model", filterReady: false,
  search: "", objectSearch: "", contextQuery: "", showLinks: false, isolate: false, scenePreset: "all", pathMode: false, pathPoints: [],
  volume: null, slice: 0, windowCenter: 40, windowWidth: 400, timelineValue: 100, temporalMode: "current", toastTimer: null, previewToken: 0,
  theme: localStorage.getItem("phasemed-theme") || "light", activeTool: "rotate", sceneYaw: .3, scenePitch: .15, sceneZoom: 1, scenePanX: 0, scenePanY: 0, drag: null,
  measurePoints: [], measureResult: null, timelineTimer: null, sliceRefreshTimer: null, modelLabDataset: null,
  holoSpin: true, holoYaw: 0, holoPitch: .15, holoZoom: 1, holoPanX: 0, holoPanY: 0, holoDrag: null, holoRaf: null, holoLast: 0,
  highlights: [], voiceState: "idle", voiceRecorder: null, voiceChunks: [], voiceTranscript: "", voiceHeld: false, voiceStartedAt: 0,
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = response.statusText || "Request failed";
    try { const body = await response.json(); message = body.detail || message; } catch {}
    throw new Error(message);
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response;
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => element.classList.remove("show"), 2800);
}

function showSheet(id) { $("scrim").classList.remove("hidden"); $(id).classList.remove("hidden"); }
function closeSheet(id) { $(id).classList.add("hidden"); if (![...document.querySelectorAll(".sheet:not(.hidden)")].length) $("scrim").classList.add("hidden"); }
function closeAllSheets() { document.querySelectorAll(".sheet").forEach((sheet) => sheet.classList.add("hidden")); $("scrim").classList.add("hidden"); }
async function showRevision(version) {
  if (!state.model) return;
  try {
    const revision = await api(`/api/models/${encodeURIComponent(state.model.id)}/revisions/${encodeURIComponent(version)}`);
    document.querySelectorAll(".revision-row").forEach((row) => row.classList.toggle("active", Number(row.dataset.revisionVersion) === Number(version)));
    const snapshot = revision.snapshot || {};
    $("revisionDetail").innerHTML = `<strong>Version ${escapeHtml(revision.version)}</strong> · ${escapeHtml(revision.reason)}<br><span>${escapeHtml(revision.created_at)} · ${escapeHtml(revision.id)}</span><div class="revision-facts"><div><span>Objects</span><strong>${number(snapshot.objects?.length || 0, 0)}</strong></div><div><span>Relationships</span><strong>${number(snapshot.relationships?.length || 0, 0)}</strong></div><div><span>Context bindings</span><strong>${number(snapshot.context_bindings?.length || 0, 0)}</strong></div></div>`;
  } catch (error) { toast(`Revision unavailable · ${error.message}`); }
}
async function openModelHistory() {
  if (!state.model) { toast("Open a PatientModel first"); return; }
  try {
    const [history, audit] = await Promise.all([api(`/api/models/${encodeURIComponent(state.model.id)}/history`), api(`/api/models/${encodeURIComponent(state.model.id)}/audit?limit=40`)]);
    $("revisionList").innerHTML = history.revisions.slice().reverse().map((revision) => `<button class="revision-row" data-revision-version="${escapeHtml(revision.version)}"><strong>v${escapeHtml(revision.version)}</strong><span>${escapeHtml(revision.reason)}</span><small>${escapeHtml(revision.created_at)}</small></button>`).join("");
    $("revisionList").querySelectorAll("[data-revision-version]").forEach((button) => button.addEventListener("click", () => showRevision(button.dataset.revisionVersion)));
    $("auditList").innerHTML = audit.events.length ? audit.events.map((event) => `<div class="audit-row"><div><strong>${escapeHtml(event.type)}</strong><span>${escapeHtml(event.subject)}</span></div><time>${escapeHtml(event.created_at)}</time></div>`).join("") : `<span class="result-placeholder">No recorded actions.</span>`;
    showSheet("historySheet");
    await showRevision(history.current_version);
  } catch (error) { toast(`History unavailable · ${error.message}`); }
}
function hasModel() { return Boolean(state.model?.id); }
function objects() { return state.viewModel?.objects || state.model?.objects || []; }
function currentObject() { return objects().find((item) => item.id === state.selectedId) || objects()[0] || null; }
function temporalLinkForCurrent(id) { return (state.model?.temporal_links || []).find((link) => link.source_object_id === id) || null; }
function priorObjectForCurrent(object) { const link = temporalLinkForCurrent(object?.id); return link ? (state.priorModel?.objects || []).find((item) => item.id === link.target_object_id) || null : null; }
function currentObjectForPrior(id) { const link = (state.model?.temporal_links || []).find((item) => item.target_object_id === id); return link ? (state.model?.objects || []).find((item) => item.id === link.source_object_id) || null : null; }
function cloneObject(object) { return object ? JSON.parse(JSON.stringify(object)) : null; }
function interpolateVector(first, second, amount) { return first.map((value, index) => Number(value || 0) + (Number(second?.[index] ?? value) - Number(value || 0)) * amount); }
function interpolatedObject(current, prior, amount) {
  const result = cloneObject(current);
  if (!result?.geometry || !prior?.geometry) return result;
  result.geometry.centroid = interpolateVector(prior.geometry.centroid, current.geometry.centroid, amount);
  result.geometry.bounding_box = { min: interpolateVector(prior.geometry.bounding_box.min, current.geometry.bounding_box.min, amount), max: interpolateVector(prior.geometry.bounding_box.max, current.geometry.bounding_box.max, amount) };
  if (prior.geometry.volume_mm3 != null && current.geometry.volume_mm3 != null) result.geometry.volume_mm3 = Number(prior.geometry.volume_mm3) + (Number(current.geometry.volume_mm3) - Number(prior.geometry.volume_mm3)) * amount;
  return result;
}
const isLungObject = (item) => /lung_|trachea|bronch|airway/i.test(item?.label || "");
const isBrainObject = (item) => /(^|[_\s-])brain($|[_\s-])|cerebell|brainstem|ventricle|thalam|cortex|cerebr/i.test(item?.label || "");
// A preset is the default anatomy filter for a model; its groups are the narrower cuts the Anatomy button cycles through.
const LUNG_GROUPS = [
  { id: "lungs", label: "Lungs", matches: isLungObject },
  { id: "lungs-right", label: "Right lung", matches: (item) => /^lung_.*_right$/i.test(item?.label || "") },
  { id: "lungs-left", label: "Left lung", matches: (item) => /^lung_.*_left$/i.test(item?.label || "") },
  { id: "airways", label: "Airways", matches: (item) => /trachea|bronch|airway/i.test(item?.label || "") },
];
const BRAIN_GROUPS = [{ id: "brain", label: "Brain", matches: isBrainObject }];
function anatomyPreset() {
  if (objects().some((item) => /^lung_/i.test(item.label || ""))) return { id: "lungs", label: "Lungs", matches: isLungObject, groups: LUNG_GROUPS };
  if (objects().some(isBrainObject)) return { id: "brain", label: "Brain", matches: isBrainObject, groups: BRAIN_GROUPS };
  if (objects().some((item) => item.type === "anatomy")) { const group = { id: "anatomy", label: "Anatomy", matches: (item) => item.type === "anatomy" }; return { ...group, groups: [group] }; }
  return null;
}
function anatomyGroups(preset = anatomyPreset()) { return (preset?.groups || (preset ? [preset] : [])).filter((group) => objects().some(group.matches)); }
function activeAnatomyGroup() { return anatomyGroups().find((group) => group.id === state.scenePreset) || null; }
// Anatomy button walks All anatomy -> each available group -> back to All.
function cycleAnatomyGroup(step = 1) {
  const groups = anatomyGroups();
  if (!groups.length) return null;
  const order = ["all", ...groups.map((group) => group.id)];
  const next = order[(Math.max(0, order.indexOf(state.scenePreset)) + step + order.length) % order.length];
  state.scenePreset = next;
  state.isolate = false;
  const group = activeAnatomyGroup();
  if (group && !group.matches(currentObject())) { const preferred = objects().find(group.matches); if (preferred) state.selectedId = preferred.id; }
  fitScene(); renderInspector(); renderRailObjects(); updateHologramControls();
  return group;
}
function resetScenePreset() { const preset = anatomyPreset(); state.scenePreset = preset?.id || "all"; return preset; }
function defaultObjectId(model) { return model?.objects?.find((item) => /^lung_(upper|lower)_lobe_(left|right)$/i.test(item.label || ""))?.id || model?.objects?.find((item) => /^brain$/i.test(item.label || ""))?.id || model?.objects?.find((item) => ["finding", "lesion"].includes(item.type))?.id || model?.objects?.find((item) => item.type !== "volume")?.id || model?.objects?.[0]?.id || null; }
function currentModelForActions() { return state.model; }
function geometryOf(object) { return object?.geometry || null; }
function boxOf(object) { return geometryOf(object)?.bounding_box || { min: [0, 0, 0], max: [1, 1, 1] }; }
function centroidOf(object) { return geometryOf(object)?.centroid || [0, 0, 0]; }
function boxDistance(a, b) {
  if (!a || !b) return Infinity;
  const aa = boxOf(a), bb = boxOf(b); let sum = 0;
  for (let index = 0; index < 3; index += 1) { const gap = aa.max[index] < bb.min[index] ? bb.min[index] - aa.max[index] : bb.max[index] < aa.min[index] ? aa.min[index] - bb.max[index] : 0; sum += gap * gap; }
  return Math.sqrt(sum);
}
function nearestLocal(id) {
  const source = objects().find((item) => item.id === id);
  if (!source) return [];
  return objects().filter((item) => item.id !== id && !(boxOf(item).min.every((value, index) => value <= boxOf(source).min[index]) && boxOf(item).max.every((value, index) => value >= boxOf(source).max[index]))).map((item) => ({ object_id: item.id, label: item.label, distance_mm: boxDistance(source, item) })).sort((a, b) => a.distance_mm - b.distance_mm).slice(0, 6);
}
function objectNeighbors(id) { const links = state.viewModel?.relationships || state.model?.relationships || []; const ids = new Set(); links.forEach((link) => { if (link.source_object_id === id) ids.add(link.target_object_id); if (link.target_object_id === id) ids.add(link.source_object_id); }); return [...ids].map((value) => objects().find((item) => item.id === value)).filter(Boolean); }

function setTheme(theme) { state.theme = theme; document.documentElement.dataset.contrast = theme === "contrast" ? "high" : "normal"; localStorage.setItem("phasemed-theme", state.theme); }

function renderStudies() { $("demoCredit")?.classList.toggle("hidden", !state.studies.some((study) => String(study.patient_id || "").startsWith("DEMO-")));
  const list = $("studyList");
  const query = state.search.toLowerCase();
  const visible = state.studies.filter((study) => (!state.filterReady || study.status === "ready") && `${study.description || ""} ${study.patient_name || ""} ${study.patient_id || ""}`.toLowerCase().includes(query));
  if (!visible.length) { list.innerHTML = `<div class="study-empty">${state.studies.length ? "No studies match this filter." : "No studies"}</div>`; return; }
  list.innerHTML = visible.map((study) => {
    const active = state.study?.id === study.id;
    const statusClass = study.status === "ready" ? "ready" : study.status === "compiling" ? "compiling" : "";
    const detail = study.status === "ready" ? `${study.image_count || 0} images · ${study.series_count || 0} series` : study.status === "compiling" ? "PatientModel is building" : `${study.image_count || 0} images · ready to compile`;
    const series = [...(study.series || [])].filter((item) => String(item.modality || "").toUpperCase() !== "SEG").sort((a, b) => (b.instance_count || 0) - (a.instance_count || 0))[0];
    const midpoint = Math.max(0, Math.floor((series?.instance_count || study.image_count || 1) / 2));
    const preview = series?.series_instance_uid ? `/api/studies/${encodeURIComponent(study.id)}/series/${encodeURIComponent(series.series_instance_uid)}/mpr?plane=axial&index=${midpoint}&window_center=40&window_width=400` : "";
    return `<article class="study-card ${active ? "active" : ""}" data-study-id="${escapeHtml(study.id)}" tabindex="0"><div class="study-preview">${preview ? `<img src="${preview}" alt="" loading="lazy" />` : `<span>${escapeHtml(study.modality || "DCM")}</span>`}<i class="study-dot ${statusClass}"></i></div><div class="study-card-body"><div class="study-card-top"><span>${escapeHtml(study.study_date || "UNDATED")}</span><b>${escapeHtml(study.modality || "DICOM")}</b></div><h3>${escapeHtml(study.description || "Imported study")}</h3><p>${escapeHtml(detail)}</p><div class="study-card-bottom"><span>${escapeHtml(study.status)}</span>${study.status === "imported" ? `<button class="text-button" data-compile-study="${escapeHtml(study.id)}">Build model</button>` : `<span>${active ? "OPEN" : "OPEN →"}</span>`}</div></div></article>`;
  }).join("");
  list.querySelectorAll("[data-study-id]").forEach((card) => { card.addEventListener("click", () => openStudy(card.dataset.studyId)); card.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openStudy(card.dataset.studyId); } }); });
  list.querySelectorAll("[data-compile-study]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); startCompile(button.dataset.compileStudy); }));
}

function renderHeader() {
  if (!state.study) {
    $("patientRoute").style.display = "none"; $("seriesControl").style.display = "none"; $("headerPatient").textContent = ""; $("studyTitle").textContent = ""; $("railPatient").textContent = "Studies"; $("seriesTitle").textContent = ""; $("modelCrumb").textContent = ""; $("studySubtitle").textContent = "Import a study"; return;
  }
  $("patientRoute").style.display = "flex"; $("seriesControl").style.display = "flex";
  const patient = state.study.patient_name || state.study.patient_id || "Local patient";
  $("headerPatient").textContent = patient; $("railPatient").textContent = patient;
  $("studyTitle").textContent = state.study.description || "Imported study";
  const series = state.volume || state.study.series?.[0];
  $("seriesTitle").textContent = series ? `${series.series_description || series.modality || state.study.modality || "DICOM"} · ${series.frame_count || series.instance_count || state.study.image_count || 0}` : `${state.study.modality || "DICOM"} source`;
  $("modelCrumb").textContent = state.model ? `PatientModel v${state.model.version || 1}` : "Uncompiled";
  const sourceLine = `${state.study.modality || "DICOM"} · ${state.study.study_date || "date not supplied"} · ${state.study.image_count || 0} instances`;
  $("studySubtitle").textContent = state.model ? `PatientModel ${state.model.version || 1} · ${sourceLine}` : `${sourceLine} · indexed locally; compile to create the model`;
}

function renderMetrics() {
  const model = state.model;
  $("metricObjects").textContent = model ? number(model.objects?.length || 0, 0) : "—";
  $("metricRelationships").textContent = model ? number(model.relationships?.length || 0, 0) : "—";
  $("metricSources").textContent = model ? number(model.sources?.length || 0, 0) : "—";
  $("metricState").textContent = model ? "Ready" : state.study ? (state.study.status === "compiling" ? "Compiling" : "Indexed") : "Waiting";
  $("metricStateDetail").textContent = model ? "Model ready" : state.study ? (state.study.status === "compiling" ? "Compiling" : "Source volume available") : "";
  $("contextImport").style.display = model ? "" : "none";
  $("exportButton").style.display = model ? "" : "none";
  $("exportButton").disabled = !model;
  $("historyButton").style.display = model ? "" : "none";
  $("linkedState").style.display = model ? "flex" : "none";
  $("linkedState").classList.toggle("ready", Boolean(model));
  $("linkedState").innerHTML = model ? `<i></i>${objects().length} objects` : "";
}

function renderCapabilities() {
  const capabilities = state.model?.capabilities || {};
  const labels = { dicom_ingestion: "DICOM ingestion", volume_metadata: "Source volume", pixel_statistics: "Pixel statistics", spatial_index: "Spatial index", validated_anatomy_segmentation: "Anatomy segmentation", mesh_generation: "Surface meshes", clinical_context: "Clinical context", temporal_registration: "Prior registration", dicomweb: "DICOMweb connector", c_store: "C-STORE adapter" };
  const keys = ["dicom_ingestion", "volume_metadata", "spatial_index", "dicomweb", "validated_anatomy_segmentation", "mesh_generation", "clinical_context", "temporal_registration", "c_store"];
  $("capabilityRows").innerHTML = keys.map((key) => { const value = capabilities[key] || "not_configured"; const kind = value === "available" ? "available" : ["partial", "demo_fixture", "configured"].includes(value) ? "partial" : "unavailable"; const mark = kind === "available" ? "✓" : kind === "partial" ? "~" : "—"; return `<div class="capability-row"><span>${labels[key]}</span><b class="${kind}" title="${escapeHtml(value)}">${mark}</b></div>`; }).join("");
}

function renderInspector() {
  const object = currentObject(); $("inspectorTitle").textContent = object?.label || "Nothing selected";
  if (!object) { $("inspectorContent").innerHTML = `<div class="inspector-empty"><span class="empty-dot"></span></div>`; return; }
  const geometry = geometryOf(object); const dims = geometry ? geometry.bounding_box.max.map((value, index) => Math.abs(value - geometry.bounding_box.min[index])) : [];
  const extent = dims.length ? Math.max(...dims) : null; const changes = object.temporal_links?.[0]?.changes || {}; const change = changes.volume_change_percent;
  const changeDetails = [changes.diameter_delta_mm == null ? "" : `diameter ${changes.diameter_delta_mm > 0 ? "+" : ""}${number(changes.diameter_delta_mm)} mm`, changes.surface_area_delta_mm2 == null ? "" : `surface ${changes.surface_area_delta_mm2 > 0 ? "+" : ""}${number(changes.surface_area_delta_mm2)} mm²`, changes.centroid_distance_mm == null ? "" : `centroid ${number(changes.centroid_distance_mm)} mm`].filter(Boolean).join(" · ");
  const neighbors = nearestLocal(object.id); const bindings = (object.context || []).length ? object.context : (state.model?.context_bindings || []).filter((binding) => binding.target_id === object.id);
  const sources = object.sources?.length ? object.sources : (state.model?.sources || []).filter((source) => bindings.some((binding) => binding.source?.id === source.id));
  const measurementLabel = object.metadata?.diameter_mm || object.metadata?.diameter ? "DIAMETER" : "MAX EXTENT";
  const measurement = object.metadata?.diameter_mm || object.metadata?.diameter || extent;
  const review = object.review_status || "unreviewed";
  $("inspectorContent").innerHTML = `<div class="object-kicker"><span class="type-pill ${object.type === "finding" || object.type === "lesion" ? "finding" : ""}">${escapeHtml(object.type.toUpperCase())}</span><span class="review-state"><i></i>${escapeHtml(review)}</span></div><div class="object-lead"><h3>${escapeHtml(object.label)}</h3><p>${escapeHtml(object.id)}</p></div><div class="measure-pair"><div class="measure-box"><span>${measurementLabel}</span><strong>${measurement == null ? "—" : number(measurement)}<small>${measurement == null ? "" : " mm"}</small></strong><small>derived from geometry</small></div><div class="measure-box change"><span>VOLUME CHANGE</span><strong>${change == null ? "—" : `${change > 0 ? "+" : ""}${number(change)}%`}</strong><small>${change == null ? "no linked prior" : changeDetails || "same object match"}</small></div></div><section class="inspector-section"><div class="section-title"><span>Geometry</span><button id="focusObject">Focus</button></div><dl class="data-pairs"><div><dt>Centroid</dt><dd>${geometry ? geometry.centroid.map((value) => number(value)).join(" · ") + " mm" : "Not available"}</dd></div><div><dt>Volume</dt><dd>${geometry?.volume_mm3 == null ? "—" : `${number(geometry.volume_mm3)} mm³`}</dd></div><div><dt>Surface</dt><dd>${geometry?.surface_area_mm2 == null ? "—" : `${number(geometry.surface_area_mm2)} mm²`}</dd></div><div><dt>Mesh</dt><dd>${escapeHtml(geometry?.mesh_id || "Not supplied")}</dd></div></dl></section><section class="inspector-section"><div class="section-title"><span>Nearest structures</span><button id="showAllNeighbors">${state.showLinks ? "Hide links" : "Show links"}</button></div><div class="neighbor-list">${neighbors.length ? neighbors.map((neighbor) => `<button class="neighbor-row" data-neighbor="${escapeHtml(neighbor.object_id)}"><i></i><span><strong>${escapeHtml(neighbor.label)}</strong><small>surface distance</small></span><em>${number(neighbor.distance_mm)} mm</em></button>`).join("") : `<span class="result-placeholder">No neighboring geometry is available.</span>`}</div></section><section class="inspector-section"><div class="section-title"><span>Bound evidence</span><button id="askModelInline">Ask model</button></div><div class="source-list">${sources.length ? sources.slice(0, 4).map((source) => `<button class="source-row" data-source-id="${escapeHtml(source.id)}"><span class="source-kind ${source.type === "note" ? "note" : source.type === "history" ? "history" : ""}">${source.type === "note" ? "N" : source.type === "history" ? "H" : "R"}</span><span><strong>${escapeHtml(source.title)}</strong><small>${escapeHtml(source.date || "Undated")}</small></span><em>view</em></button>`).join("") : `<span class="result-placeholder">No context is bound to this object.</span>`}</div></section><section class="inspector-section inspector-actions"><button class="outline-button" id="isolateObject">${state.isolate ? "Show all" : "Isolate"}</button><button class="solid-button" id="reviewObject">Review</button></section><section class="inspector-section"><div class="section-title"><span>Review state</span><span class="review-state">Human verification</span></div><div class="review-row"><button data-review="confirmed">Confirm</button><button data-review="modified">Modify</button><button data-review="rejected">Reject</button></div></section>`;
  $("focusObject").addEventListener("click", () => { state.isolate = false; state.showLinks = true; drawAll(); toast(`${object.label} focused across linked views`); });
  $("showAllNeighbors").addEventListener("click", () => { state.showLinks = !state.showLinks; drawAll(); renderInspector(); });
  $("isolateObject").addEventListener("click", () => { state.isolate = !state.isolate; drawAll(); renderInspector(); });
  $("askModelInline").addEventListener("click", () => showSheet("aiSheet"));
  $("reviewObject").addEventListener("click", () => reviewObject("confirmed"));
  $("inspectorContent").querySelectorAll("[data-neighbor]").forEach((button) => button.addEventListener("click", () => selectObject(button.dataset.neighbor)));
  $("inspectorContent").querySelectorAll("[data-source-id]").forEach((button) => button.addEventListener("click", () => openSource(button.dataset.sourceId)));
  $("inspectorContent").querySelectorAll("[data-review]").forEach((button) => button.addEventListener("click", () => reviewObject(button.dataset.review)));
}

function boundsFor(items = objects()) {
  const geometryItems = items.filter((item) => geometryOf(item)); if (!geometryItems.length) return { min: [0, 0, 0], max: [100, 100, 100] };
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity]; geometryItems.forEach((item) => { const box = boxOf(item); for (let i = 0; i < 3; i += 1) { min[i] = Math.min(min[i], box.min[i]); max[i] = Math.max(max[i], box.max[i]); } });
  for (let i = 0; i < 3; i += 1) if (max[i] - min[i] < 1) max[i] = min[i] + 1;
  return { min, max };
}
function fitCanvas(canvas) { const rect = canvas.getBoundingClientRect(); const ratio = window.devicePixelRatio || 1; const width = Math.max(1, Math.round(rect.width * ratio)); const height = Math.max(1, Math.round(rect.height * ratio)); if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; } const context = canvas.getContext("2d"); context.setTransform(ratio, 0, 0, ratio, 0, 0); return { context, width: rect.width, height: rect.height }; }
// One orthographic turntable camera shared by the WebGL meshes, 2D overlays, picking and unprojection.
// World is patient LPS mm. At yaw = pitch = 0 the view is anterior: patient-left on screen-right, superior up.
const CAM_DEFAULT = { yaw: .3, pitch: .15 };
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
function sceneBounds(all = objects().filter((item) => geometryOf(item))) { const solid = all.filter((item) => item.type !== "volume"); return boundsFor(solid.length ? solid : all); }
function camFor(bounds, width, height, yaw = state.sceneYaw, pitch = state.scenePitch, zoom = state.sceneZoom, panX = state.scenePanX, panY = state.scenePanY) { const c = [0, 1, 2].map((i) => (bounds.min[i] + bounds.max[i]) / 2); const D = Math.hypot(...[0, 1, 2].map((i) => bounds.max[i] - bounds.min[i])) || 1; const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch); return { c, D, width, height, panX, panY, s: zoom * 1.1 * Math.min(width, height) / D, R: [cy, -sy, 0], U: [sy * sp, cy * sp, cp], T: [-sy * cp, -cy * cp, sp] }; }
function project(point, cam) { const q = [point[0] - cam.c[0], point[1] - cam.c[1], point[2] - cam.c[2]]; return [cam.width / 2 + cam.panX + cam.s * dot3(cam.R, q), cam.height / 2 + cam.panY - cam.s * dot3(cam.U, q), dot3(cam.T, q)]; }
function unproject(point, cam, anchor) { const X = (point.x - cam.width / 2 - cam.panX) / cam.s; const Y = (cam.height / 2 + cam.panY - point.y) / cam.s; const Z = anchor ? dot3(cam.T, [anchor[0] - cam.c[0], anchor[1] - cam.c[1], anchor[2] - cam.c[2]]) : 0; return [0, 1, 2].map((i) => cam.c[i] + X * cam.R[i] + Y * cam.U[i] + Z * cam.T[i]); }
function camMatrices(cam) { const rows = [cam.R.map((v) => v * cam.s / (cam.width / 2)), cam.U.map((v) => v * cam.s / (cam.height / 2)), cam.T.map((v) => -v / (.55 * cam.D))]; const t = rows.map((row) => -dot3(row, cam.c)); t[0] += cam.panX / (cam.width / 2); t[1] -= cam.panY / (cam.height / 2); return { mat: new Float32Array([rows[0][0], rows[1][0], rows[2][0], 0, rows[0][1], rows[1][1], rows[2][1], 0, rows[0][2], rows[1][2], rows[2][2], 0, t[0], t[1], t[2], 1]), view: new Float32Array([cam.R[0], cam.U[0], cam.T[0], cam.R[1], cam.U[1], cam.T[1], cam.R[2], cam.U[2], cam.T[2]]) }; }
function boxCorners(object) { const box = boxOf(object); return [0, 1, 2, 3, 4, 5, 6, 7].map((n) => [n & 1 ? box.max[0] : box.min[0], n & 2 ? box.max[1] : box.min[1], n & 4 ? box.max[2] : box.min[2]]); }
function projectSize(object, cam) { const points = boxCorners(object).map((corner) => project(corner, cam)); const xs = points.map((p) => p[0]); const ys = points.map((p) => p[1]); return [Math.max(8, Math.max(...xs) - Math.min(...xs)), Math.max(8, Math.max(...ys) - Math.min(...ys))]; }
// Display-only tint so neighbouring structures read apart; object type colours still drive lists and glyphs.
const MESH_TINTS = [[/nodule|lesion|tumou?r|mass/i, "#e8b04a"], [/brain|cerebell|cortex|cerebr/i, "#d7a7a9"], [/lung/i, "#7cc6bb"], [/heart|atri|ventric/i, "#d98d8d"], [/aort|arter/i, "#d5645c"], [/vein|cava/i, "#6f8fd0"], [/trache|bronch|airway/i, "#8db6e6"], [/spine|vertebra|rib|stern|bone/i, "#d9d2bb"]];
const HIGHLIGHT_COLOR = "#ff3b30";
const isHighlighted = (item) => state.highlights.includes(item?.id);
const meshColor = (item) => (isHighlighted(item) ? HIGHLIGHT_COLOR : MESH_TINTS.find(([pattern]) => pattern.test(item.label || ""))?.[1] || typeColor(item));
const hexRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
function meshAlpha(item) { const lift = document.documentElement.dataset.contrast === "high" ? .12 : 0; if (isHighlighted(item)) return .95; if (item.id === state.selectedId) return item.type === "finding" || item.type === "lesion" ? .97 : .92; if (item.type === "finding" || item.type === "lesion") return 1; const group = activeAnatomyGroup() || anatomyPreset(); return (item.type === "region" ? .06 : group?.matches(item) ? .42 : .16) + lift; }
const entryColor = (entry, base) => isHighlighted(entry.item) ? HIGHLIGHT_COLOR : entry.style === "changed" ? "#b5645d" : entry.style === "new" ? "#b78a35" : entry.style === "resolved" ? "#899590" : base(entry.item);
// Plan entries can come from the current or the prior PatientModel; interpolated (morph) copies belong to neither and have no mesh.
function modelIdFor(item) { if (state.priorModel?.objects?.includes(item)) return state.priorModel.id; if (state.model?.objects?.includes(item)) return state.model.id; return null; }
// Composites the WebGL meshes for temporal plan entries into a 2D canvas. The selection paints last so it reads through its neighbours.
function meshLayer(ctx, cam, entries, offsetX = 0, offsetY = 0) { const drawn = new Set(); if (!window.MeshView?.ok) return drawn; const items = []; entries.forEach((entry) => { const item = entry.item; const modelId = modelIdFor(item); if (item.type === "volume" || !modelId || !MeshView.ensure(modelId, item)) return; drawn.add(entry); const selected = item.id === state.selectedId && !entry.ghost; items.push({ key: MeshView.key(modelId, item.id), color: hexRgb(entryColor(entry, meshColor)), alpha: entry.ghost ? .2 : meshAlpha(item), depth: selected || isHighlighted(item) ? Infinity : project(centroidOf(item), cam)[2] }); }); if (items.length && MeshView.draw({ width: cam.width, height: cam.height, dpr: window.devicePixelRatio || 1, viewports: [{ x: 0, y: 0, w: cam.width, h: cam.height, ...camMatrices(cam) }], items })) ctx.drawImage(MeshView.canvas, offsetX, offsetY, cam.width, cam.height); return drawn; }
let drawQueued = false;
function requestDraw() { if (drawQueued) return; drawQueued = true; requestAnimationFrame(() => { drawQueued = false; drawAll(); }); }
if (window.MeshView) MeshView.onReady = requestDraw;

function temporalRenderPlan() {
  const current = (state.model?.objects || []).filter((item) => geometryOf(item));
  const prior = (state.priorModel?.objects || []).filter((item) => geometryOf(item));
  if (!state.priorModel || state.temporalMode === "current") return current.map((item) => ({ item }));
  if (state.temporalMode === "prior") return prior.map((item) => ({ item }));
  if (state.temporalMode === "overlay") return [...prior.map((item) => ({ item, ghost: true, style: "prior" })), ...current.map((item) => ({ item }))];
  if (state.temporalMode === "difference") {
    const changedCurrent = current.filter((item) => ["changed_from", "new"].includes(temporalLinkForCurrent(item.id)?.type));
    const baselineCurrent = current.filter((item) => !changedCurrent.some((changed) => changed.id === item.id));
    const resolvedPrior = prior.filter((item) => (state.model?.temporal_links || []).some((link) => link.type === "resolved" && link.target_object_id === item.id));
    return [...baselineCurrent.map((item) => ({ item, ghost: true, style: "baseline" })), ...resolvedPrior.map((item) => ({ item, ghost: true, style: "resolved" })), ...changedCurrent.map((item) => ({ item, style: temporalLinkForCurrent(item.id)?.type === "new" ? "new" : "changed" }))];
  }
  const amount = clamp(Number(state.timelineValue) / 100, 0, 1);
  return current.map((item) => ({ item: interpolatedObject(item, priorObjectForCurrent(item), amount), style: "morph" }));
}

function scenePlan(plan = temporalRenderPlan().filter((entry) => geometryOf(entry.item))) {
  if (state.isolate) return plan.filter((entry) => entry.item.id === state.selectedId);
  const group = activeAnatomyGroup();
  if (group) return plan.filter((entry) => group.matches(entry.item) || isHighlighted(entry.item));
  return plan;
}

function updateSceneControls() {
  const preset = anatomyPreset();
  const group = activeAnatomyGroup();
  const active = Boolean(group);
  $("sceneAnatomy").hidden = !preset;
  $("sceneAnatomyLabel").textContent = group?.label || (preset ? "All anatomy" : "Anatomy");
  $("sceneAnatomy").title = anatomyGroups().length ? `Cycle anatomy: All · ${anatomyGroups().map((item) => item.label).join(" · ")}` : "Anatomy";
  $("sceneAnatomy")?.classList.toggle("active", active);
  $("sceneAnatomy")?.setAttribute("aria-pressed", active ? "true" : "false");
  $("sceneNeighbors")?.classList.toggle("active", state.showLinks);
  $("sceneNeighbors")?.setAttribute("aria-pressed", state.showLinks ? "true" : "false");
  $("sceneIsolate")?.classList.toggle("active", state.isolate);
  $("sceneIsolate")?.setAttribute("aria-pressed", state.isolate ? "true" : "false");
}

function fitScene() { state.sceneZoom = 1; state.scenePanX = 0; state.scenePanY = 0; drawAll(); }

function zoomScene(factor, anchor = null) {
  const canvas = $("sceneCanvas");
  const previous = state.sceneZoom;
  const next = clamp(previous * factor, .18, 8);
  if (anchor && canvas && next !== previous) {
    const ratio = next / previous;
    const centerX = canvas.clientWidth / 2;
    const centerY = canvas.clientHeight / 2;
    state.scenePanX = anchor.x - centerX - ratio * (anchor.x - centerX - state.scenePanX);
    state.scenePanY = anchor.y - centerY - ratio * (anchor.y - centerY - state.scenePanY);
  }
  state.sceneZoom = next;
  drawAll();
}

function drawSceneOn(canvas, compact = false) {
  const { context: ctx, width, height } = fitCanvas(canvas); ctx.clearRect(0, 0, width, height); ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--scene").trim() || "#f7f9f8"; ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(28,62,55,.08)"; ctx.lineWidth = 1; for (let x = 0; x < width; x += compact ? 32 : 48) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke(); } for (let y = 0; y < height; y += compact ? 32 : 48) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
  const plan = temporalRenderPlan().filter((entry) => geometryOf(entry.item)); const shown = (compact ? plan : scenePlan(plan)).sort((a, b) => (a.item.type === "volume" ? -1 : 0) - (b.item.type === "volume" ? -1 : 0)); const all = shown.map((entry) => entry.item); if (!all.length) return;
  const bounds = sceneBounds(all); const cam = camFor(bounds, width, height);
  const pointFor = (item) => project(centroidOf(item), cam); const ink = getComputedStyle(document.documentElement).getPropertyValue("--text").trim() || "#1d2b28";
  const inGl = meshLayer(ctx, cam, shown);
  const drawBox = (item) => { const corners = boxCorners(item).map((corner) => project(corner, cam)); ctx.save(); ctx.globalAlpha = item.id === state.selectedId ? .7 : .22; ctx.strokeStyle = typeColor(item); ctx.lineWidth = 1; ctx.beginPath(); for (let a = 0; a < 8; a += 1) for (const bit of [1, 2, 4]) if (!(a & bit)) { ctx.moveTo(corners[a][0], corners[a][1]); ctx.lineTo(corners[a | bit][0], corners[a | bit][1]); } ctx.stroke(); ctx.restore(); };
  // Fallback glyph for entries whose mesh is unavailable, still loading, or interpolated (morph).
  const drawObject = (entry) => { const item = entry.item; const ghost = Boolean(entry.ghost); const center = pointFor(item); const size = projectSize(item, cam); const selected = item.id === state.selectedId && !ghost; const color = entryColor(entry, typeColor); ctx.save(); ctx.translate(center[0], center[1]); ctx.globalAlpha = ghost ? .28 : selected ? 1 : .72; ctx.fillStyle = color; ctx.strokeStyle = color;
    if (item.type === "finding" || item.type === "lesion") { ctx.beginPath(); ctx.arc(0, 0, Math.max(7, Math.min(size[0], size[1]) * .28), 0, Math.PI * 2); ghost ? ctx.stroke() : ctx.fill(); }
    else { ctx.beginPath(); ctx.ellipse(0, 0, size[0] * .5, size[1] * .5, 0, 0, Math.PI * 2); ghost ? ctx.stroke() : ctx.fill(); }
    if (selected) { ctx.strokeStyle = ink; ctx.lineWidth = 1.2; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.ellipse(0, 0, size[0] * .5 + 8, size[1] * .5 + 8, 0, 0, Math.PI * 2); ctx.stroke(); }
    ctx.restore();
  };
  shown.forEach((entry) => { if (entry.item.type === "volume") { if (!entry.ghost) drawBox(entry.item); } else if (!inGl.has(entry)) drawObject(entry); });
  const selected = currentObject(); if (state.showLinks && selected) { const anchor = pointFor(selected); ctx.save(); ctx.strokeStyle = "rgba(60,140,125,.42)"; ctx.setLineDash([4, 6]); objectNeighbors(selected.id).forEach((neighbor) => { if (!geometryOf(neighbor)) return; const target = pointFor(neighbor); ctx.globalAlpha = target[2] < anchor[2] ? .5 : 1; ctx.beginPath(); ctx.moveTo(anchor[0], anchor[1]); ctx.lineTo(target[0], target[1]); ctx.stroke(); }); ctx.restore(); }
  const screen = (point) => point.world ? project(point.world, cam) : [point.x, point.y];
  if (state.pathPoints.length) { const points = state.pathPoints.map(screen); ctx.save(); ctx.strokeStyle = "#efc77e"; ctx.lineWidth = 2; ctx.setLineDash([7, 5]); ctx.beginPath(); points.forEach((point, index) => index ? ctx.lineTo(point[0], point[1]) : ctx.moveTo(point[0], point[1])); ctx.stroke(); ctx.setLineDash([]); points.forEach((point) => { ctx.fillStyle = "#efc77e"; ctx.beginPath(); ctx.arc(point[0], point[1], 4, 0, Math.PI * 2); ctx.fill(); }); ctx.restore(); }
  if (state.measurePoints.length) { const points = state.measurePoints.map(screen); ctx.save(); ctx.strokeStyle = "#2f9c8d"; ctx.fillStyle = "#2f9c8d"; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]); ctx.beginPath(); points.forEach((point, index) => index ? ctx.lineTo(point[0], point[1]) : ctx.moveTo(point[0], point[1])); ctx.stroke(); ctx.setLineDash([]); points.forEach((point) => { ctx.beginPath(); ctx.arc(point[0], point[1], 4, 0, Math.PI * 2); ctx.fill(); }); ctx.restore(); }
  if (!compact && selected) { const point = pointFor(selected); const callout = $("sceneCallout"); callout.classList.remove("hidden"); callout.style.left = `${clamp(point[0] + 18, 20, width - 190)}px`; callout.style.top = `${clamp(point[1] - 20, 76, height - 76)}px`; $("calloutTitle").textContent = selected.label; $("calloutText").textContent = `${selected.type} · click to inspect`; } else $("sceneCallout").classList.add("hidden");
  return { bounds, width, height, cam };
}
function updateOrientation(cam) { const widget = $("orientWidget"); if (!widget || !cam) return; const axes = { L: [1, 0, 0], R: [-1, 0, 0], P: [0, 1, 0], A: [0, -1, 0], S: [0, 0, 1], I: [0, 0, -1] }; widget.querySelectorAll("[data-axis]").forEach((label) => { const axis = axes[label.dataset.axis]; const x = dot3(cam.R, axis), y = dot3(cam.U, axis), toward = dot3(cam.T, axis); label.style.left = `${24 + 20 * x}px`; label.style.top = `${24 - 20 * y}px`; label.style.opacity = Math.hypot(x, y) < .25 ? 0 : .35 + .65 * (.5 + .5 * toward); }); const bar = document.querySelector(".model-scale"); if (bar) { const length = [100, 50, 20, 10, 5].find((mm) => mm * cam.s <= 120) || 5; bar.querySelector("span").style.width = `${Math.round(length * cam.s)}px`; bar.querySelector("b").textContent = `${length} mm`; } }
function drawScene() { const result = drawSceneOn($("sceneCanvas")); updateOrientation(result?.cam); const total = objects().filter((item) => geometryOf(item)).length; const shown = scenePlan().length; const preset = anatomyPreset(); $("sceneCount").textContent = total ? `${shown} shown · ${total} objects` : "No geometry loaded"; $("sceneSummary").textContent = preset && state.scenePreset === preset.id && !state.isolate ? `${preset.label} · segmented anatomy` : currentObject()?.label || "Select an object to inspect it"; updateSceneControls(); return result; }

function drawProcedure() { const canvas = $("procedureCanvas"); if (!canvas) return; drawSceneOn(canvas, true); }
// Glass alphas tuned for the light workspace wash out on black, so lift them without flattening the selection contrast.
const holoAlpha = (alpha) => (alpha >= .9 ? 1 : Math.min(.92, alpha * 1.3 + .05));
function drawHologram() {
  const canvas = $("hologramCanvas"); const { context: ctx, width, height } = fitCanvas(canvas);
  ctx.clearRect(0, 0, width, height); ctx.fillStyle = "#000"; ctx.fillRect(0, 0, width, height);
  // Same plan the workspace model view draws, so the hologram inherits the anatomy filter, the selection and the glass look.
  const available = scenePlan().filter((entry) => entry.item.type !== "volume");
  if (!available.length) return;
  const bounds = sceneBounds(available.map((entry) => entry.item)); const stage = Math.min(width, height); const tile = stage * .34 * state.holoZoom; const radius = stage * .285 * state.holoZoom; const centerX = width / 2 + state.holoPanX; const centerY = height / 2 + state.holoPanY;
  const views = [
    { x: centerX, y: centerY - radius, rotation: 0, yaw: state.holoYaw },
    { x: centerX + radius, y: centerY, rotation: Math.PI / 2, yaw: state.holoYaw + Math.PI / 2 },
    { x: centerX, y: centerY + radius, rotation: Math.PI, yaw: state.holoYaw + Math.PI },
    { x: centerX - radius, y: centerY, rotation: -Math.PI / 2, yaw: state.holoYaw + Math.PI * 1.5 },
  ];
  views.forEach((view) => {
    const cam = camFor(bounds, tile, tile, view.yaw, state.holoPitch, 1.28, 0, 0);
    // Each part keeps its own tint so the hologram reads as distinct anatomy, not one blue blob.
    const items = []; const fallback = [];
    available.forEach((entry) => {
      const item = entry.item; const modelId = modelIdFor(item); const selected = item.id === state.selectedId;
      if (window.MeshView?.ok && modelId && MeshView.ensure(modelId, item)) {
        items.push({ key: MeshView.key(modelId, item.id), color: hexRgb(entryColor(entry, meshColor)), alpha: entry.ghost ? holoAlpha(.2) : holoAlpha(meshAlpha(item)), depth: selected || isHighlighted(item) ? Infinity : project(centroidOf(item), cam)[2] });
      } else fallback.push({ item, selected });
    });
    let rendered = false;
    if (items.length) { const matrices = camMatrices(cam); rendered = MeshView.draw({ width: tile, height: tile, dpr: window.devicePixelRatio || 1, viewports: [{ x: 0, y: 0, w: tile, h: tile, ...matrices }], items }); }
    ctx.save(); ctx.translate(view.x, view.y); ctx.rotate(view.rotation); ctx.globalCompositeOperation = "screen"; ctx.filter = "brightness(1.4) saturate(1.5) drop-shadow(0 0 9px rgba(120,200,255,.55)) drop-shadow(0 0 22px rgba(90,150,255,.32))";
    if (rendered) ctx.drawImage(MeshView.canvas, -tile / 2, -tile / 2, tile, tile);
    fallback.forEach(({ item, selected }) => { const center = project(centroidOf(item), cam); const size = projectSize(item, cam); const [r, g, b] = hexRgb(meshColor(item)).map((v) => Math.round(v * 255)); ctx.fillStyle = `rgba(${r},${g},${b},${holoAlpha(meshAlpha(item))})`; ctx.beginPath(); ctx.ellipse(center[0] - tile / 2, center[1] - tile / 2, Math.max(10, size[0] * .42), Math.max(10, size[1] * .42), 0, 0, Math.PI * 2); ctx.fill(); });
    ctx.restore();
  });
}
// The hologram spins on its own so the Pepper's Ghost pyramid shows every side without a hand on the mouse.
const HOLO_SPIN_SPEED = .35; // radians per second
const HOLO_ZOOM_RANGE = [.4, 2.6];
function holoTick(timestamp) {
  state.holoRaf = null;
  if (state.mode !== "hologram" || !state.holoSpin || state.holoDrag) { state.holoLast = 0; return; }
  const delta = state.holoLast ? Math.min(.1, (timestamp - state.holoLast) / 1000) : 0;
  state.holoLast = timestamp;
  state.holoYaw = (state.holoYaw + delta * HOLO_SPIN_SPEED) % (Math.PI * 2);
  drawHologram();
  state.holoRaf = requestAnimationFrame(holoTick);
}
function startHoloSpin() { if (state.holoRaf || !state.holoSpin || state.mode !== "hologram") return; state.holoLast = 0; state.holoRaf = requestAnimationFrame(holoTick); }
function stopHoloSpin() { if (state.holoRaf) cancelAnimationFrame(state.holoRaf); state.holoRaf = null; state.holoLast = 0; }
function updateHologramControls() {
  const spin = $("hologramSpin"); if (!spin) return;
  spin.classList.toggle("active", state.holoSpin);
  spin.setAttribute("aria-pressed", state.holoSpin ? "true" : "false");
  spin.querySelector("span").textContent = state.holoSpin ? "Pause" : "Spin";
  const anatomy = $("hologramAnatomy"); if (!anatomy) return;
  const groups = anatomyGroups(); const group = activeAnatomyGroup();
  anatomy.hidden = !groups.length;
  anatomy.classList.toggle("active", Boolean(group));
  anatomy.querySelector("span").textContent = group?.label || "All anatomy";
  updateVoiceControl();
}
function setHoloSpin(spinning) { state.holoSpin = spinning; updateHologramControls(); if (spinning) startHoloSpin(); else { stopHoloSpin(); drawHologram(); } }
let holoDrawQueued = false;
function requestHoloDraw() { if (holoDrawQueued) return; holoDrawQueued = true; requestAnimationFrame(() => { holoDrawQueued = false; drawHologram(); }); }
function zoomHologram(factor) { state.holoZoom = clamp(state.holoZoom * factor, HOLO_ZOOM_RANGE[0], HOLO_ZOOM_RANGE[1]); requestHoloDraw(); }
function resetHologramView() { state.holoYaw = 0; state.holoPitch = CAM_DEFAULT.pitch; state.holoZoom = 1; state.holoPanX = 0; state.holoPanY = 0; requestHoloDraw(); }

// --- Hold-to-talk ---------------------------------------------------------------
// Hold space (or hold the button) to record; release to send. The clip is transcribed by
// ElevenLabs on the server (the API key never reaches the browser) and the transcript is
// resolved to objects by backend/voice.py. The microphone is open only while held.
const VOICE_LABELS = { idle: "Hold space to speak", recording: "Listening · release to send", working: "Working…" };
const VOICE_MAX_MS = 10000;
const VOICE_MIN_MS = 350;
function updateVoiceControl(message) {
  const button = $("hologramVoice"); if (!button) return;
  button.classList.toggle("recording", state.voiceState === "recording");
  button.classList.toggle("working", state.voiceState === "working");
  button.disabled = state.voiceState === "working";
  button.querySelector("span").textContent = VOICE_LABELS[state.voiceState] || VOICE_LABELS.idle;
  const hint = $("hologramVoiceHint");
  if (hint) hint.textContent = message || (state.voiceTranscript ? `heard “${state.voiceTranscript}”` : "hold space speak · drag rotate · scroll zoom");
}
function setVoiceState(next, message) { state.voiceState = next; updateVoiceControl(message); }
// Loading the local speech model takes a moment; do it when the hologram opens, not on the first hold.
let voiceWarmed = false;
function warmVoiceEngine() { if (voiceWarmed) return; voiceWarmed = true; whenIdle(() => { api("/api/voice/warm", { method: "POST" }).catch(() => { voiceWarmed = false; }); }); }
function clearHighlights() { state.highlights = []; state.voiceTranscript = ""; drawAll(); renderInspector(); renderRailObjects(); updateVoiceControl(); }
// Press and release are separate so a release that lands before the microphone is granted
// still ends the take instead of leaving it recording.
async function startVoiceCapture() {
  if (state.voiceHeld || state.voiceState !== "idle") return;
  if (!state.model) { toast("Open a study first"); return; }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { toast("This browser cannot record audio"); return; }
  state.voiceHeld = true;
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { state.voiceHeld = false; setVoiceState("idle", "microphone permission denied"); toast("Microphone permission denied"); return; }
  if (!state.voiceHeld) { stream.getTracks().forEach((track) => track.stop()); return; }
  const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((type) => MediaRecorder.isTypeSupported?.(type)) || "";
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  state.voiceChunks = [];
  state.voiceStartedAt = performance.now();
  recorder.addEventListener("dataavailable", (event) => { if (event.data?.size) state.voiceChunks.push(event.data); });
  recorder.addEventListener("stop", () => {
    stream.getTracks().forEach((track) => track.stop());
    const blob = new Blob(state.voiceChunks, { type: recorder.mimeType || "audio/webm" });
    const held = performance.now() - state.voiceStartedAt;
    state.voiceChunks = [];
    state.voiceRecorder = null;
    if (held < VOICE_MIN_MS) { setVoiceState("idle", "hold space while you speak"); return; }
    sendVoiceClip(blob);
  });
  state.voiceRecorder = recorder;
  recorder.start();
  setVoiceState("recording", "say e.g. “highlight right lung”");
  setTimeout(() => { if (state.voiceRecorder === recorder && recorder.state === "recording") stopVoiceCapture(); }, VOICE_MAX_MS);
}
function stopVoiceCapture() {
  state.voiceHeld = false;
  if (state.voiceRecorder?.state === "recording") state.voiceRecorder.stop();
}
async function sendVoiceClip(blob) {
  state.voiceRecorder = null;
  if (!blob?.size || !state.model) { setVoiceState("idle"); return; }
  setVoiceState("working");
  const form = new FormData();
  form.append("file", blob, blob.type.includes("mp4") ? "clip.mp4" : "clip.webm");
  try {
    const result = await api(`/api/patient-models/${encodeURIComponent(state.model.id)}/voice-command`, { method: "POST", body: form });
    applyVoiceCommand(result);
  } catch (error) { setVoiceState("idle", "command failed"); toast(`Voice command failed · ${error.message}`); }
}
function applyVoiceCommand(result) {
  state.voiceTranscript = result?.transcript || "";
  if (result?.intent === "clear") { state.highlights = []; toast("Highlight cleared"); }
  else if (result?.targets?.length) { state.highlights = result.targets; toast(`Highlighted ${result.summary}`); }
  else { state.highlights = []; toast(`Heard “${state.voiceTranscript}” · no matching structure`); }
  setVoiceState("idle");
  drawAll(); renderInspector(); renderRailObjects();
}
function drawAll() { if (state.model && state.mode === "model") drawScene(); if (state.model && state.mode === "slices") drawSceneOn($("mprModelCanvas"), true); if (state.model && state.mode === "procedure") drawProcedure(); if (state.model && state.mode === "hologram") drawHologram(); }

function mapWorld(point, canvas) { if (point.world) return point.world; const visible = scenePlan().map((entry) => entry.item); const cam = camFor(sceneBounds(visible), Math.max(1, canvas.clientWidth), Math.max(1, canvas.clientHeight)); const selected = currentObject(); return unproject(point, cam, selected?.geometry ? centroidOf(selected) : null); }
function pointFromEvent(event, canvas) { const rect = canvas.getBoundingClientRect(); return { x: event.clientX - rect.left, y: event.clientY - rect.top }; }
function hitTest(point, canvas) { const all = scenePlan().map((entry) => entry.item); const cam = camFor(sceneBounds(all), Math.max(1, canvas.clientWidth), Math.max(1, canvas.clientHeight)); let hit = null; let best = Infinity; all.forEach((item) => { if (item.type === "volume") return; const projected = project(centroidOf(item), cam); const size = projectSize(item, cam); const radius = Math.max(20, size[0] * .5, size[1] * .5); const distance = Math.hypot(point.x - projected[0], point.y - projected[1]); const score = distance / radius; if (distance < radius && score < best) { best = score; hit = item; } }); return hit; }

async function selectObject(id) { if (!objects().some((item) => item.id === id)) return; state.selectedId = id; state.isolate = false; state.pathPoints = []; const object = currentObject(); const group = activeAnatomyGroup(); if (group && !group.matches(object)) state.scenePreset = "all"; if (object?.geometry && state.volume?.instances?.length) { const targetZ = object.geometry.centroid[2]; let nearestIndex = 0; let nearestDelta = Infinity; state.volume.instances.forEach((instance, index) => { const delta = Math.abs(Number(instance.z || index) - targetZ); if (delta < nearestDelta) { nearestDelta = delta; nearestIndex = index; } }); state.slice = nearestIndex; $("sliceInput").value = String(nearestIndex); await refreshSlices(); } renderInspector(); renderRailObjects(); renderAnalysisPanels(); drawAll(); updateCrosshair(); toast(`${currentObject().label} selected · linked views updated`); }

async function openSeriesChooser() {
  if (!state.study) { toast("Open a study first"); return; }
  const series = (state.study.series || []).filter((item) => !["SEG", "SR"].includes(String(item.modality || "").toUpperCase()));
  $("seriesOptions").innerHTML = series.length ? series.map((item) => `<button class="series-option ${item.series_instance_uid === state.volume?.series_instance_uid ? "active" : ""}" data-series-uid="${escapeHtml(item.series_instance_uid)}"><span><strong>${escapeHtml(item.description || item.series_description || item.modality || "Image series")}</strong><span>${escapeHtml(item.modality || "DICOM")} · ${escapeHtml(item.series_instance_uid)} · ${number(item.instance_count || 0, 0)} instances</span></span><em>${item.series_instance_uid === state.volume?.series_instance_uid ? "Selected" : "Open"}</em></button>`).join("") : `<span class="result-placeholder">No renderable image series found.</span>`;
  showSheet("seriesSheet");
  $("seriesOptions").querySelectorAll("[data-series-uid]").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    try { await loadVolume(state.study, button.dataset.seriesUid); closeSheet("seriesSheet"); renderHeader(); toast("Viewer series changed"); } catch (error) { toast(`Series could not be opened · ${error.message}`); button.disabled = false; }
  }));
}

function updateCrosshair() { const object = currentObject(); const bounds = boundsFor(objects()); if (!object) return; const point = centroidOf(object); const x = clamp((point[0] - bounds.min[0]) / Math.max(1, bounds.max[0] - bounds.min[0]) * 100, 12, 88); const y = clamp((point[1] - bounds.min[1]) / Math.max(1, bounds.max[1] - bounds.min[1]) * 100, 12, 88); const depth = Math.max(1, Number(state.volume?.shape?.[0] || 1)); const sliceY = clamp((1 - state.slice / Math.max(1, depth - 1)) * 100, 4, 96); $("axialCrosshair").style.left = `${x}%`; $("axialCrosshair").style.top = `${y}%`; $("coronalCrosshair").style.left = `${x}%`; $("coronalCrosshair").style.top = `${sliceY}%`; $("sagittalCrosshair").style.left = `${y}%`; $("sagittalCrosshair").style.top = `${sliceY}%`; }

function selectImageObject(event, plane) { if (!state.model || !state.volume?.shape) return; const rect = event.currentTarget.getBoundingClientRect(); const u = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1); const v = clamp((event.clientY - rect.top) / Math.max(1, rect.height), 0, 1); const first = state.volume.instances?.[0] || {}; const origin = first.position || [0, 0, 0]; const spacing = state.volume.pixel_spacing || [1, 1]; const rows = state.volume.shape[1] || 1; const columns = state.volume.shape[2] || 1; const zValues = (state.volume.instances || []).map((item) => Number(item.z)).filter((value) => Number.isFinite(value)); const zMin = zValues.length ? Math.min(...zValues) : origin[2] || 0; const zMax = zValues.length ? Math.max(...zValues) : zMin + (state.volume.shape[0] || 1) * (state.volume.slice_thickness || 1); let point; if (plane === "axial") point = [origin[0] + u * columns * spacing[1], origin[1] + v * rows * spacing[0], Number(state.volume.instances?.[state.slice]?.z ?? zMin)]; else if (plane === "coronal") { const yIndex = Math.floor(rows / 2); point = [origin[0] + u * columns * spacing[1], origin[1] + yIndex * spacing[0], zMax - v * (zMax - zMin)]; } else { const xIndex = Math.floor(columns / 2); point = [origin[0] + xIndex * spacing[1], origin[1] + u * rows * spacing[0], zMax - v * (zMax - zMin)]; } const candidates = objects().filter((item) => { const box = boxOf(item); return box.min.every((value, index) => point[index] >= value && point[index] <= box.max[index]); }).sort((a, b) => (a.geometry?.volume_mm3 || Infinity) - (b.geometry?.volume_mm3 || Infinity)); if (candidates[0]) selectObject(candidates[0].id); }

async function loadVolume(study, seriesUid = null) { state.viewerStudy = study || null; state.volume = null; ["axialImage", "coronalImage", "sagittalImage"].forEach((id) => { $(id).removeAttribute("src"); }); if (!study) return; try { const query = seriesUid ? `?series_uid=${encodeURIComponent(seriesUid)}` : ""; const volume = await api(`/api/studies/${encodeURIComponent(study.id)}/volume${query}`); state.volume = volume; if (seriesUid) state.slice = 0; if (!volume.renderable) { $("sliceMeta").textContent = "Series pixels not renderable"; return; } if (String(volume.modality || "").toUpperCase() === "CT") { state.windowCenter = 40; state.windowWidth = 400; $("windowCenterInput").value = "40"; $("windowWidthInput").value = "400"; } const count = Math.max(1, volume.frame_count || volume.shape?.[0] || 1); $("sliceInput").max = String(count - 1); state.slice = clamp(state.slice, 0, count - 1); $("sliceInput").value = String(state.slice); await refreshSlices(); } catch (error) { $("sliceMeta").textContent = "No renderable series"; throw error; } }
function updateSliceReadout() { const position = `${state.slice + 1} / ${state.volume?.shape?.[0] || 1}`; $("sliceOutput").textContent = position; $("axialCaption").textContent = `slice ${state.slice + 1}`; $("coronalCaption").textContent = `linked slice ${state.slice + 1}`; $("sagittalCaption").textContent = `linked slice ${state.slice + 1}`; $("axialWl").textContent = `W:${number(state.windowWidth, 0)} / L:${number(state.windowCenter, 0)}`; $("windowCenterOutput").textContent = `${number(state.windowCenter, 0)} HU`; $("windowWidthOutput").textContent = `${number(state.windowWidth, 0)} HU`; updateCrosshair(); }
async function refreshSlices(planes = ["axial", "coronal", "sagittal"]) { if (!state.volume?.renderable || !(state.viewerStudy || state.study)) return; const series = encodeURIComponent(state.volume.series_instance_uid); const viewerStudy = state.viewerStudy || state.study; const root = `/api/studies/${encodeURIComponent(viewerStudy.id)}/series/${series}/mpr`; const suffix = `&window_center=${encodeURIComponent(state.windowCenter)}&window_width=${encodeURIComponent(state.windowWidth)}&v=${Date.now()}`; $("sliceMeta").textContent = `${state.volume.shape?.join(" × ") || "volume"} · ${state.volume.modality || "DICOM"} · WC ${number(state.windowCenter, 0)} / WW ${number(state.windowWidth, 0)}`; const requests = [["axialImage", "axial", state.slice], ["coronalImage", "coronal", Math.floor((state.volume.shape?.[1] || 1) / 2)], ["sagittalImage", "sagittal", Math.floor((state.volume.shape?.[2] || 1) / 2)]].filter(([, plane]) => planes.includes(plane)); requests.forEach(([id, plane, index]) => { const image = $(id); const url = `${root}?plane=${plane}&index=${index}${suffix}`; image.classList.remove("loaded"); image.onload = () => { if (image.getAttribute("src") === url) image.classList.add("loaded"); }; image.src = url; }); updateSliceReadout(); }
function scheduleSliceRefresh(planes, delay = 70) { clearTimeout(state.sliceRefreshTimer); updateSliceReadout(); state.sliceRefreshTimer = setTimeout(() => refreshSlices(planes), delay); }

function renderTimeline() {
  const model = state.model; const timeline = model?.timeline || [];
  $("timelineRail").innerHTML = timeline.length ? timeline.map((entry, index) => `<div class="timeline-node ${index < timeline.length - 1 ? "prior" : ""}" style="left:${timeline.length === 1 ? 50 : (index / (timeline.length - 1)) * 100}%"><div class="timeline-node-label"><strong>${escapeHtml(entry.label || entry.study_id)}</strong>${escapeHtml(entry.date || "")}</div></div>`).join("") : "";
  const hasPrior = Boolean(model?.metadata?.prior_model_id || (model?.temporal_links || []).length) && Boolean(state.priorModel);
  $("timelineStatus").textContent = hasPrior ? `${model.temporal_links?.length || 0} object links` : "No linked prior";
  document.querySelectorAll("[data-temporal-mode]").forEach((button) => { const active = button.dataset.temporalMode === state.temporalMode; button.classList.toggle("active", active); button.setAttribute("aria-selected", active ? "true" : "false"); button.disabled = !hasPrior && button.dataset.temporalMode !== "current"; });
  const notes = { current: "Current study geometry and current evidence.", prior: "Prior linked model; selection follows the persisted object link.", overlay: "Current geometry with linked prior geometry shown as a quiet outline.", difference: "Only persisted changed, new, and resolved objects are emphasized.", morph: "Interpolated visualization between models. This is not a biological trajectory or a clinical estimate." };
  $("temporalModeNote").textContent = hasPrior ? notes[state.temporalMode] : "No prior PatientModel is linked to this study.";
  const links = model?.temporal_links || [];
  if (!links.length) { $("timelineDetail").innerHTML = `<span class="timeline-empty">No comparison available.</span>`; return; }
  const changeLinks = links.filter((link) => link.type !== "same_as_prior");
  const rows = changeLinks.slice(0, 8).map((link) => { const object = (model.objects || []).find((item) => item.id === link.source_object_id) || (state.priorModel?.objects || []).find((item) => item.id === link.target_object_id); const changes = link.changes || {}; const details = [changes.volume_change_percent == null ? "" : `volume ${changes.volume_change_percent > 0 ? "+" : ""}${number(changes.volume_change_percent)}%`, changes.diameter_delta_mm == null ? "" : `diameter ${changes.diameter_delta_mm > 0 ? "+" : ""}${number(changes.diameter_delta_mm)} mm`, changes.surface_area_delta_mm2 == null ? "" : `surface ${changes.surface_area_delta_mm2 > 0 ? "+" : ""}${number(changes.surface_area_delta_mm2)} mm²`, changes.centroid_distance_mm == null ? "" : `centroid ${number(changes.centroid_distance_mm)} mm`].filter(Boolean).join(" · "); return `<button class="temporal-change-row" data-temporal-object="${escapeHtml(object?.id || link.source_object_id)}"><span class="temporal-change-type ${escapeHtml(link.type)}">${escapeHtml(link.type.replaceAll("_", " "))}</span><strong>${escapeHtml(object?.label || link.source_object_id)}</strong><small>${escapeHtml(details || "No numeric geometry delta")}</small><em>›</em></button>`; }).join("");
  $("timelineDetail").innerHTML = `<div class="change-grid"><div><span>Linked</span><strong>${links.filter((link) => link.type === "same_as_prior").length}</strong></div><div><span>Changed</span><strong>${links.filter((link) => link.type === "changed_from").length}</strong></div><div><span>New</span><strong>${links.filter((link) => link.type === "new").length}</strong></div><div><span>Resolved</span><strong>${links.filter((link) => link.type === "resolved").length}</strong></div></div>${rows ? `<div class="temporal-change-list">${rows}</div>` : `<p class="timeline-empty">All linked objects are unchanged within the compiler thresholds.</p>`}`;
  $("timelineDetail").querySelectorAll("[data-temporal-object]").forEach((button) => button.addEventListener("click", () => { setMode("model"); selectObject(button.dataset.temporalObject); }));
}

function renderObjectBrowser(filter = state.objectSearch) { const grouped = {}; objects().filter((item) => `${item.label} ${item.type} ${item.metadata?.region || ""}`.toLowerCase().includes(filter.toLowerCase())).forEach((item) => { const group = item.metadata?.group || (item.type === "finding" || item.type === "lesion" ? "Findings" : item.type === "region" ? "Regions" : "Anatomy"); (grouped[group] ||= []).push(item); }); $("objectBrowser").innerHTML = Object.entries(grouped).map(([group, items]) => `<div class="object-group">${escapeHtml(group)}</div>${items.map((item) => `<button class="browser-row" data-browser-object="${escapeHtml(item.id)}"><i class="object-color" style="background:${typeColor(item)}"></i><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.type)}</small></span></button>`).join("")}`).join("") || `<div class="study-empty">No objects match.</div>`; $("objectBrowser").querySelectorAll("[data-browser-object]").forEach((button) => button.addEventListener("click", () => { closeSheet("objectSheet"); selectObject(button.dataset.browserObject); })); }

function renderContextView(results = null) {
  const bindings = results || state.model?.context_bindings || [];
  if (!bindings.length) {
    $("contextList").innerHTML = `<div class="context-empty"><span class="empty-dot"></span><strong>No bound context yet.</strong><p>Import a FHIR Bundle, report, note, or plain context JSON to attach evidence to this PatientModel.</p></div>`;
    return;
  }
  $("contextList").innerHTML = bindings.map((entry) => {
    const binding = entry.binding || entry;
    const source = entry.source || binding.source || {};
    const target = entry.target || objects().find((item) => item.id === binding.target_id);
    const targetLabel = target?.label || binding.target_id || binding.target_type || "Patient";
    return `<article class="context-card"><div class="context-card-top"><span class="context-kind">${escapeHtml(source.type || "context")}</span><span class="review-state"><i></i>${escapeHtml(binding.review_status || "unreviewed")}</span></div><h3>${escapeHtml(source.title || "Imported context")}</h3><p>${escapeHtml(binding.evidence || source.excerpt || "No excerpt supplied")}</p><div class="context-card-meta"><span>${escapeHtml(source.date || "Undated")}</span><strong>${escapeHtml(targetLabel)}</strong></div><div class="context-card-actions"><button data-context-source="${escapeHtml(source.id || "")}">View source</button>${binding.target_type === "object" && binding.target_id ? `<button data-context-object="${escapeHtml(binding.target_id)}">View object</button>` : ""}<button data-context-review="confirmed" data-binding-id="${escapeHtml(binding.id || "")}">Confirm</button><button data-context-review="rejected" data-binding-id="${escapeHtml(binding.id || "")}">Reject</button></div></article>`;
  }).join("");
  $("contextList").querySelectorAll("[data-context-source]").forEach((button) => button.addEventListener("click", () => openSource(button.dataset.contextSource)));
  $("contextList").querySelectorAll("[data-context-object]").forEach((button) => button.addEventListener("click", () => selectObject(button.dataset.contextObject)));
  $("contextList").querySelectorAll("[data-context-review]").forEach((button) => button.addEventListener("click", async () => { await reviewBinding(button.dataset.contextReview, button.dataset.bindingId); renderContextView(); }));
}

function renderRailObjects() {
  const list = $("objectRail"); const items = objects();
  list.innerHTML = items.length ? items.map((item) => `<button class="rail-row ${item.id === state.selectedId ? "active" : ""}" data-rail-object="${escapeHtml(item.id)}"><i style="background:${typeColor(item)}"></i><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.type)}</small></span><em>›</em></button>`).join("") : `<div class="rail-placeholder">Open a compiled model to browse objects.</div>`;
  list.querySelectorAll("[data-rail-object]").forEach((button) => button.addEventListener("click", () => selectObject(button.dataset.railObject)));
}

function renderRailSources() {
  const list = $("sourceRail"); const sources = state.model?.sources || [];
  list.innerHTML = sources.length ? sources.map((source) => `<button class="rail-row" data-rail-source="${escapeHtml(source.id)}"><i class="source-marker"></i><span><strong>${escapeHtml(source.title || "Source")}</strong><small>${escapeHtml(source.type || "evidence")} · ${escapeHtml(source.date || "undated")}</small></span><em>›</em></button>`).join("") : `<div class="rail-placeholder">No provenance sources loaded.</div>`;
  list.querySelectorAll("[data-rail-source]").forEach((button) => button.addEventListener("click", () => openSource(button.dataset.railSource)));
}

function renderAnalysisPanels() {
  const object = currentObject(); const graph = $("graphInspector"); const evidence = $("evidenceInspector");
  if (!object) { graph.innerHTML = `<div class="rail-placeholder">Select an object to inspect its spatial graph.</div>`; evidence.innerHTML = `<div class="rail-placeholder">No evidence selected.</div>`; return; }
  const relations = (state.viewModel?.relationships || state.model?.relationships || []).filter((link) => link.source_object_id === object.id || link.target_object_id === object.id);
  graph.innerHTML = `<div class="graph-root"><i style="background:${typeColor(object)}"></i><span><strong>${escapeHtml(object.label)}</strong><small>${escapeHtml(object.type)}</small></span></div>${relations.length ? relations.map((link) => { const otherId = link.source_object_id === object.id ? link.target_object_id : link.source_object_id; const other = objects().find((item) => item.id === otherId); const direction = link.source_object_id === object.id ? "out" : "in"; return `<button class="graph-edge" data-graph-object="${escapeHtml(otherId)}"><span>${direction === "out" ? "→" : "←"}</span><div><strong>${escapeHtml(link.type || "related")}</strong><small>${escapeHtml(other?.label || otherId)}${link.distance_mm == null ? "" : ` · ${number(link.distance_mm)} mm`}</small></div></button>`; }).join("") : `<div class="rail-placeholder">No persisted relationships for this object.</div>`}`;
  graph.querySelectorAll("[data-graph-object]").forEach((button) => button.addEventListener("click", () => selectObject(button.dataset.graphObject)));
  const bindings = (object.context || []).length ? object.context : (state.model?.context_bindings || []).filter((binding) => binding.target_id === object.id);
  const sources = object.sources?.length ? object.sources : (state.model?.sources || []).filter((source) => bindings.some((binding) => (binding.source?.id || binding.source_id) === source.id));
  evidence.innerHTML = sources.length ? sources.map((source) => `<button class="evidence-row" data-evidence-source="${escapeHtml(source.id)}"><span>${escapeHtml((source.type || "E").slice(0, 1).toUpperCase())}</span><div><strong>${escapeHtml(source.title || "Evidence")}</strong><small>${escapeHtml(source.date || "Undated")} · provenance attached</small></div><em>Open</em></button>`).join("") : `<div class="rail-placeholder">No evidence is bound to ${escapeHtml(object.label)}.</div>`;
  evidence.querySelectorAll("[data-evidence-source]").forEach((button) => button.addEventListener("click", () => openSource(button.dataset.evidenceSource)));
}

function renderWorkspace() { renderHeader(); renderMetrics(); renderCapabilities(); renderInspector(); renderTimeline(); renderContextView(); renderStudies(); renderRailObjects(); renderRailSources(); renderAnalysisPanels(); const hasStudy = Boolean(state.study); const showEmpty = !hasStudy || (!state.model && state.mode === "model"); document.body.dataset.mode = state.mode; $("emptyState").classList.toggle("hidden", !showEmpty); $("commandbar").style.display = hasStudy ? "flex" : "none"; $("rightRail").style.display = hasStudy ? "flex" : "none"; $("workstation").classList.toggle("no-study", !hasStudy); document.querySelectorAll(".model-only-control").forEach((control) => control.classList.toggle("hidden", state.mode !== "model")); ["modelView", "sliceView", "timelineView", "contextView", "procedureView", "hologramView"].forEach((id) => $(id).classList.remove("active")); if (!showEmpty) { const target = state.mode === "model" ? "modelView" : state.mode === "slices" ? "sliceView" : state.mode === "timeline" ? "timelineView" : state.mode === "context" ? "contextView" : state.mode === "procedure" ? "procedureView" : "hologramView"; $(target).classList.add("active"); } $("timelineStrip").style.display = hasStudy && (state.mode === "model" || state.mode === "slices") ? "grid" : "none"; drawAll(); updateCrosshair(); }

async function openStudy(id) { try { state.study = await api(`/api/studies/${encodeURIComponent(id)}`); state.viewerStudy = state.study; state.model = null; state.priorModel = null; state.priorStudy = null; state.viewModel = null; state.selectedId = null; state.temporalMode = "current"; state.timelineValue = 100; state.scenePreset = "all"; state.highlights = []; state.voiceTranscript = ""; state.scenePanX = 0; state.scenePanY = 0; state.sceneZoom = 1; renderWorkspace(); await loadVolume(state.study); if (state.study.model_id) { state.model = await loadModelCached(state.study.model_id); state.viewModel = state.model; state.selectedId = defaultObjectId(state.model); resetScenePreset(); if (state.model.metadata?.prior_model_id) { try { state.priorModel = await loadModelCached(state.model.metadata.prior_model_id); state.priorStudy = await api(`/api/studies/${encodeURIComponent(state.priorModel.study_id)}`); } catch { state.priorModel = null; state.priorStudy = null; } } window.MeshView?.setModel([state.model.id, state.priorModel?.id]); warmCurrentModel(); renderWorkspace(); await loadVolume(state.study); toast("PatientModel opened from local storage"); } else { toast("Study indexed · build the PatientModel to continue"); } } catch (error) { toast(`Could not open study · ${error.message}`); } }

// Demo preloading: model JSON is cached in memory and every mesh URL is pulled into the HTTP cache ahead of time,
// so opening another study (or the hologram tab, which draws every object rather than the current preset) never stalls mid-demo.
const modelCache = new Map();
const meshPrefetched = new Set();
let prefetchRunning = false;
async function loadModelCached(id) { if (!id) return null; if (modelCache.has(id)) return modelCache.get(id); const model = await api(`/api/patient-models/${encodeURIComponent(id)}`); modelCache.set(id, model); return model; }
function meshUrlsFor(model) { return (model?.objects || []).filter((item) => item.type !== "volume" && item.geometry?.mesh_id).map((item) => `/api/patient-models/${encodeURIComponent(model.id)}/objects/${encodeURIComponent(item.id)}/mesh`); }
const whenIdle = (fn) => (window.requestIdleCallback ? requestIdleCallback(fn, { timeout: 2000 }) : setTimeout(fn, 200));
function warmCurrentModel() {
  if (!window.MeshView?.ok) return;
  [state.model, state.priorModel].filter(Boolean).forEach((model) => MeshView.warm(model.id, model.objects));
}
// Fetch-only warmup: the bytes land in the HTTP cache, so a later GPU upload skips the network.
async function prefetchStudies() {
  if (prefetchRunning) return;
  prefetchRunning = true;
  const idle = () => new Promise((resolve) => whenIdle(resolve));
  // Never compete with the open study's own mesh uploads.
  const settle = async () => { while (window.MeshView?.pending?.()) await new Promise((resolve) => setTimeout(resolve, 250)); };
  try {
    await settle();
    const ordered = [...state.studies].sort((a, b) => Number(a.id === state.study?.id) - Number(b.id === state.study?.id));
    for (const study of ordered) {
      if (!study.model_id) continue;
      let model;
      try { model = await loadModelCached(study.model_id); } catch { continue; }
      const priorId = model?.metadata?.prior_model_id;
      if (priorId) { try { await loadModelCached(priorId); } catch {} }
      for (const modelId of [model?.id, priorId].filter(Boolean)) {
        const target = modelCache.get(modelId);
        for (const url of meshUrlsFor(target)) {
          if (meshPrefetched.has(url)) continue;
          meshPrefetched.add(url);
          try { await fetch(url, { cache: "force-cache" }).then((response) => response.arrayBuffer()); } catch { meshPrefetched.delete(url); }
          await idle(); await settle();
        }
      }
    }
  } finally { prefetchRunning = false; }
}

async function loadStudies() { try { state.studies = await api("/api/studies"); renderStudies(); if (!state.study && state.studies.length) { const ready = state.studies.find((study) => study.status === "ready") || state.studies[0]; await openStudy(ready.id); } else renderWorkspace(); } catch (error) { renderWorkspace(); toast(`Workspace unavailable · ${error.message}`); } }

async function importFiles(files) { if (!files?.length) return; const form = new FormData(); [...files].forEach((file) => form.append("files", file, file.name)); try { toast("Indexing DICOM study…"); const result = await api("/api/studies/import", { method: "POST", body: form }); state.studies.unshift(result.study); await openStudy(result.study.id); startCompile(result.study.id); } catch (error) { toast(`Import failed · ${error.message}`); } }

function openCompile(jobId) { showSheet("compileSheet"); $("compileProgress").style.width = "0%"; $("compileProgressText").textContent = "0%"; $("compileMessage").textContent = "Preparing study…"; const stages = ["Validate DICOM study", "Reconstruct source volume", "Extract image statistics", "Generate object candidates", "Build spatial index", "Calculate relationships", "Bind available context", "Register compatible prior models", "Persist PatientModel"]; $("compileStageList").innerHTML = stages.map((stage, index) => `<div class="compile-stage" data-stage-index="${index}"><i>${index + 1}</i><span>${stage}</span><small></small></div>`).join(""); $("compileSheet").dataset.jobId = jobId; }
async function startCompile(studyId) { try { const result = await api(`/api/studies/${encodeURIComponent(studyId)}/compile`, { method: "POST" }); openCompile(result.job.id); pollCompile(result.job.id); } catch (error) { toast(`Compiler could not start · ${error.message}`); } }
async function pollCompile(jobId) { try { const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`); $("compileProgress").style.width = `${job.progress}%`; $("compileProgressText").textContent = `${job.progress}%`; $("compileMessage").textContent = job.message || job.stage; const rows = [...document.querySelectorAll(".compile-stage")]; const current = rows.findIndex((row) => row.querySelector("span").textContent === job.stage); rows.forEach((row, index) => { const done = index < current || job.status === "completed"; row.classList.toggle("active", index === current); row.classList.toggle("done", done); row.querySelector("i").textContent = done ? "✓" : String(index + 1); row.querySelector("small").textContent = done ? "done" : index === current ? "running" : ""; }); if (job.status === "completed") { closeSheet("compileSheet"); const study = await api(`/api/studies/${encodeURIComponent(job.study_id)}`); state.study = study; state.model = await api(`/api/patient-models/${encodeURIComponent(study.model_id)}`); state.priorModel = null; state.priorStudy = null; state.temporalMode = "current"; state.timelineValue = 100; state.viewModel = state.model; state.selectedId = defaultObjectId(state.model); if (state.model.metadata?.prior_model_id) { try { state.priorModel = await api(`/api/patient-models/${encodeURIComponent(state.model.metadata.prior_model_id)}`); state.priorStudy = await api(`/api/studies/${encodeURIComponent(state.priorModel.study_id)}`); } catch {} } renderWorkspace(); await loadVolume(study); toast("PatientModel ready · derived data persisted"); return; } if (job.status === "failed") { toast(`Compilation stopped · ${job.error || "unknown error"}`); return; } setTimeout(() => pollCompile(jobId), 380); } catch (error) { toast(`Compiler status unavailable · ${error.message}`); } }

async function reviewObject(status) { const object = currentObject(); if (!object || !state.model) return; try { const updated = await api(`/api/models/${encodeURIComponent(state.model.id)}/objects/${encodeURIComponent(object.id)}/review`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ status }) }); object.review_status = updated.review_status; state.model = await api(`/api/patient-models/${encodeURIComponent(state.model.id)}`); state.viewModel = state.model; renderWorkspace(); toast(`${object.label} marked ${status}`); } catch (error) { toast(`Review failed · ${error.message}`); } }

async function reviewBinding(status, bindingId) { if (!state.model) return; try { await api(`/api/models/${encodeURIComponent(state.model.id)}/context/${encodeURIComponent(bindingId)}/review`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ status }) }); state.model = await api(`/api/patient-models/${encodeURIComponent(state.model.id)}`); state.viewModel = state.model; renderWorkspace(); toast(`Context mapping marked ${status}`); } catch (error) { toast(`Context review failed · ${error.message}`); } }
function openSource(sourceId) { const source = (state.model?.sources || []).find((item) => item.id === sourceId) || (currentObject()?.sources || []).find((item) => item.id === sourceId); if (!source) return; $("sourceTitle").textContent = source.title || "Source evidence"; $("sourceMeta").textContent = `${source.date || "Undated"} · ${source.type || "source"}`; $("sourceExcerpt").textContent = source.excerpt || source.text || "No excerpt supplied by source."; const binding = (state.model?.context_bindings || []).find((item) => item.source?.id === source.id); $("sourceBinding").innerHTML = binding ? `<div>Bound to <strong>${escapeHtml(binding.target_type)}${binding.target_id ? ` · ${escapeHtml(binding.target_id)}` : ""}</strong> · ${escapeHtml(binding.method)} · relevance ${Math.round(binding.relevance * 100)}% · review <strong>${escapeHtml(binding.review_status)}</strong></div><div class="source-provenance"><span>Algorithm</span><strong>${escapeHtml(binding.algorithm_version || "not recorded")}</strong></div><div class="binding-actions"><button data-binding-review="confirmed">Confirm</button><button data-binding-review="modified">Modify</button><button data-binding-review="rejected">Reject</button>${binding.target_type === "object" && binding.target_id ? `<button data-binding-object="${escapeHtml(binding.target_id)}">View object</button>` : ""}</div>` : `Provenance: ${escapeHtml(source.uri || source.id)}`; showSheet("sourceSheet"); $("sourceBinding").querySelectorAll("[data-binding-review]").forEach((button) => button.addEventListener("click", () => reviewBinding(button.dataset.bindingReview, binding.id))); const objectButton = $("sourceBinding").querySelector("[data-binding-object]"); if (objectButton) objectButton.addEventListener("click", () => { closeSheet("sourceSheet"); selectObject(objectButton.dataset.bindingObject); }); }

async function importContextFile(file) { if (!file || !state.model) { toast("Open a compiled PatientModel first"); return; } try { const payload = JSON.parse(await file.text()); const result = await api(`/api/models/${encodeURIComponent(state.model.id)}/context`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) }); state.model = await api(`/api/patient-models/${encodeURIComponent(result.model_id)}`); state.viewModel = state.model; renderWorkspace(); toast(`${result.bindings.length} context bindings added with provenance`); } catch (error) { toast(`Context import failed · ${error.message}`); } }

async function exportModel() { if (!state.model) return; try { const response = await api(`/api/patient-models/${encodeURIComponent(state.model.id)}/export`); const blob = new Blob([JSON.stringify(response, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `phasemed-${state.model.id.replaceAll(":", "-")}.json`; link.click(); URL.revokeObjectURL(url); toast("PatientModel JSON exported"); } catch (error) { toast(`Export failed · ${error.message}`); } }

function downloadJson(payload, filename) { const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
function dicomValue(item, tag) { const value = item?.[tag]?.Value?.[0]; return typeof value === "object" ? [value.Alphabetic, value.Ideographic, value.Phonetic].filter(Boolean).join(" / ") : value ?? ""; }
async function openGateway() {
  showSheet("gatewaySheet"); $("gatewayCapabilities").innerHTML = `<span>Checking configured sources…</span>`; $("gatewayStudies").innerHTML = `<div class="rail-placeholder">Loading QIDO studies…</div>`; $("gatewayStaged").innerHTML = `<div class="rail-placeholder">Loading staged studies…</div>`;
  try {
    const [capabilities, staged] = await Promise.all([api("/api/dicomweb/capabilities"), api("/api/dicomweb/cstore/studies")]);
    $("gatewayCapabilities").innerHTML = Object.entries(capabilities).map(([key, value]) => `<div><span>${escapeHtml(key.replaceAll("_", " "))}</span><strong class="${value === "available" ? "available" : "unavailable"}">${escapeHtml(value)}</strong></div>`).join("");
    if (capabilities.dicomweb === "available") { const studies = await api("/api/dicomweb/studies"); $("gatewayStudies").innerHTML = studies.length ? studies.map((study) => { const uid = dicomValue(study, "0020000D"); const patient = dicomValue(study, "00100010") || dicomValue(study, "00100020") || "Remote patient"; const description = dicomValue(study, "00081030") || "Remote DICOM study"; return `<article class="gateway-row"><div><strong>${escapeHtml(patient)}</strong><span>${escapeHtml(description)}</span><small>${escapeHtml(uid)}</small></div><button data-gateway-import="dicomweb" data-study-uid="${escapeHtml(uid)}">Import</button></article>`; }).join("") : `<div class="rail-placeholder">No studies returned by QIDO-RS.</div>`; } else $("gatewayStudies").innerHTML = `<div class="rail-placeholder">Set PHASEMED_DICOMWEB_URL to connect QIDO/WADO/STOW.</div>`;
    $("gatewayStaged").innerHTML = staged.length ? staged.map((study) => `<article class="gateway-row"><div><strong>${escapeHtml(study.patient_name || study.patient_id || "Staged patient")}</strong><span>${escapeHtml(study.description || "C-STORE study")}</span><small>${escapeHtml(study.study_instance_uid || study.study_uid || "")}</small></div><button data-gateway-import="cstore" data-study-uid="${escapeHtml(study.study_instance_uid || study.study_uid || "")}">Promote</button></article>`).join("") : `<div class="rail-placeholder">No studies are waiting in C-STORE staging.</div>`;
    $("gatewaySheet").querySelectorAll("[data-gateway-import]").forEach((button) => button.addEventListener("click", async () => { button.disabled = true; button.textContent = "Importing…"; try { const prefix = button.dataset.gatewayImport === "cstore" ? "/api/dicomweb/cstore/studies" : "/api/dicomweb/studies"; const result = await api(`${prefix}/${encodeURIComponent(button.dataset.studyUid)}/import`, { method: "POST" }); closeSheet("gatewaySheet"); await loadStudies(); if (result.study?.id) await openStudy(result.study.id); toast("Study imported from imaging gateway"); } catch (error) { button.disabled = false; button.textContent = "Retry"; toast(`Gateway import failed · ${error.message}`); } }));
  } catch (error) { $("gatewayCapabilities").innerHTML = `<span>Gateway unavailable · ${escapeHtml(error.message)}</span>`; }
}

function renderModelAlgorithmOptions(task) {
  const options = task === "regression"
    ? `<option value="auto">Auto-select validated algorithm</option><option value="linear-regression">Linear regression</option><option value="random-forest">Random forest</option>`
    : `<option value="auto">Auto-select validated algorithm</option><option value="binary-logistic-regression">Logistic regression</option><option value="random-forest">Random forest</option>`;
  $("modelLabAlgorithm").innerHTML = options;
}

async function openModelLab() {
  state.modelLabDataset = null;
  showSheet("modelLabSheet");
  $("modelLabRows").innerHTML = `<div class="rail-placeholder">Loading compiled PatientModels…</div>`;
  $("modelLabSchema").innerHTML = `<span class="rail-placeholder">Loading feature schema…</span>`;
  try {
    const [schema, algorithms] = await Promise.all([api("/api/model-lab/schema"), api("/api/model-lab/algorithms")]);
    $("modelLabSchema").innerHTML = schema.feature_schema.map((feature) => `<div><span>${escapeHtml(feature.label)}</span><small>${escapeHtml(feature.unit)}</small></div>`).join("");
    renderModelAlgorithmOptions($("modelLabTask").value);
    const studies = state.studies.filter((study) => study.model_id);
    $("modelLabRows").innerHTML = studies.length ? studies.map((study) => `<div class="model-lab-row"><div><strong>${escapeHtml(study.patient_name || study.patient_id || "Patient")}</strong><span>${escapeHtml(study.description || study.modality || "Compiled study")}</span><small>${escapeHtml(study.id)}</small></div><input class="model-lab-label" data-lab-label="${escapeHtml(study.model_id)}" aria-label="Outcome label for ${escapeHtml(study.id)}" type="number" step="any" placeholder="Exclude" /></div>`).join("") : `<div class="rail-placeholder">Compile at least one study before assembling a cohort.</div>`;
    document.querySelectorAll("[data-lab-label]").forEach((input) => input.addEventListener("input", () => { state.modelLabDataset = null; }));
    $("modelLabTask").onchange = () => { state.modelLabDataset = null; renderModelAlgorithmOptions($("modelLabTask").value); };
    const latest = algorithms[0];
    if (latest) $("modelLabResult").innerHTML = `<div><span>Latest algorithm</span><strong>${escapeHtml(latest.name)}</strong><small>${latest.training?.row_count || 0} labeled models · accuracy ${Math.round((latest.training?.metrics?.accuracy || 0) * 100)}%</small></div>`;
  } catch (error) {
    $("modelLabRows").innerHTML = `<div class="rail-placeholder">Algorithm workbench unavailable · ${escapeHtml(error.message)}</div>`;
  }
}

async function analyzeModelLabCohort() {
  const button = $("analyzeCohort");
  const modelIds = state.studies.filter((study) => study.model_id).map((study) => study.model_id);
  if (modelIds.length < 2) { toast("Compile at least two PatientModels for cohort analysis"); return; }
  button.disabled = true;
  button.textContent = "Analyzing…";
  try {
    const analysis = await api("/api/model-lab/cohort-analysis", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ model_ids: modelIds, clusters: Math.min(4, Math.max(2, Math.round(Math.sqrt(modelIds.length)))) }) });
    const outlier = [...(analysis.rows || [])].sort((a, b) => b.anomaly_score - a.anomaly_score)[0];
    const groupSummary = (analysis.clusters || []).map((cluster) => `${cluster.row_count} in group ${cluster.cluster + 1}`).join(" · ");
    $("modelLabResult").innerHTML = `<div><span>Cohort analysis ready</span><strong>${analysis.row_count} models · ${analysis.clusters.length} groups</strong><small>${escapeHtml(groupSummary)} · PCA explains ${Math.round((analysis.projection?.explained_variance_ratio || []).reduce((sum, value) => sum + value, 0) * 100)}% of standardized variance</small><code>${escapeHtml(analysis.id)}</code>${outlier ? `<small class="model-lab-validation">Highest anomaly score: ${escapeHtml(outlier.model_id)} · ${number(outlier.anomaly_score, 2)}</small>` : ""}</div>`;
    toast("Cohort structure analyzed and persisted");
  } catch (error) { toast(`Cohort analysis failed · ${error.message}`); }
  button.disabled = false;
  button.textContent = "Analyze cohort";
}

async function importModelLabels(file) {
  if (!file) return;
  const button = $("importModelLabels");
  button.disabled = true;
  button.textContent = "Importing…";
  try {
    const body = new FormData();
    body.append("file", file);
    body.append("task", $("modelLabTask").value);
    body.append("name", $("modelLabName").value || "Imported PatientModel cohort");
    const dataset = await api("/api/model-lab/datasets/import", { method: "POST", body });
    state.modelLabDataset = dataset;
    const labels = Object.fromEntries(dataset.rows.map((row) => [row.model_id, row.label]));
    document.querySelectorAll("[data-lab-label]").forEach((input) => { if (labels[input.dataset.labLabel] !== undefined) input.value = String(labels[input.dataset.labLabel]); });
    toast(`${dataset.rows.length} labels imported into the cohort`);
  } catch (error) { toast(`Label import failed · ${error.message}`); }
  button.disabled = false;
  button.textContent = "Import labels";
}

async function crossValidateModel(algorithmId, datasetId, button) {
  button.disabled = true;
  button.textContent = "Validating…";
  try {
    const result = await api(`/api/model-lab/algorithms/${encodeURIComponent(algorithmId)}/cross-validate`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ dataset_id: datasetId, folds: 5 }) });
    const metrics = result.metrics || {};
    const summary = metrics.rmse == null ? `${Math.round((metrics.accuracy || 0) * 100)}% accuracy · ${Math.round((metrics.precision || 0) * 100)}% precision · ${Math.round((metrics.recall || 0) * 100)}% recall` : `RMSE ${number(metrics.rmse, 2)} · MAE ${number(metrics.mae, 2)} · R² ${number(metrics.r2, 2)}`;
    button.parentElement.insertAdjacentHTML("beforeend", `<small class="model-lab-validation">${result.fold_count}-fold validation · ${summary}</small>`);
    button.remove();
    toast("Cross-validation completed and recorded");
  } catch (error) {
    toast(`Cross-validation failed · ${error.message}`);
    button.disabled = false;
    button.textContent = "Cross-validate cohort";
  }
}

async function batchPredictModel(algorithmId, datasetId, button) {
  button.disabled = true;
  button.textContent = "Running…";
  try {
    const dataset = await api(`/api/model-lab/datasets/${encodeURIComponent(datasetId)}`);
    const result = await api(`/api/model-lab/algorithms/${encodeURIComponent(algorithmId)}/batch-predict`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ model_ids: dataset.rows.map((row) => row.model_id) }) });
    const outputs = result.results || [];
    const classifier = outputs.some((item) => item.probability_positive != null);
    const summary = classifier ? `${outputs.filter((item) => item.prediction === 1).length}/${outputs.length} positive · mean probability ${Math.round(outputs.reduce((sum, item) => sum + Number(item.probability_positive || 0), 0) / Math.max(1, outputs.length) * 100)}%` : `mean prediction ${number(outputs.reduce((sum, item) => sum + Number(item.prediction || 0), 0) / Math.max(1, outputs.length), 2)} · ${outputs.length} models`;
    button.parentElement.insertAdjacentHTML("beforeend", `<small class="model-lab-validation">Cohort inference · ${summary}</small>`);
    button.remove();
    toast("Cohort inference completed");
  } catch (error) {
    toast(`Cohort inference failed · ${error.message}`);
    button.disabled = false;
    button.textContent = "Run cohort inference";
  }
}

async function inspectModelLabQuality() {
  const button = $("inspectQuality");
  const task = $("modelLabTask").value;
  try {
    let dataset = state.modelLabDataset?.task === task ? state.modelLabDataset : null;
    if (!dataset) {
      const labels = {};
      document.querySelectorAll("[data-lab-label]").forEach((input) => { if (input.value !== "") labels[input.dataset.labLabel] = Number(input.value); });
      const modelIds = Object.keys(labels);
      if (modelIds.length < 2) { toast("Choose labels for at least two compiled models first"); return; }
      dataset = await api("/api/model-lab/datasets", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ task, name: $("modelLabName").value || "Quality review cohort", model_ids: modelIds, labels }) });
      state.modelLabDataset = dataset;
    }
    button.disabled = true; button.textContent = "Checking…";
    const datasets = await api("/api/model-lab/datasets");
    const baseline = datasets.find((candidate) => candidate.id !== dataset.id && candidate.task === task);
    const qualityPath = baseline ? `/api/model-lab/datasets/${encodeURIComponent(dataset.id)}/quality?baseline_dataset_id=${encodeURIComponent(baseline.id)}` : `/api/model-lab/datasets/${encodeURIComponent(dataset.id)}/quality`;
    const quality = await api(qualityPath);
    const flagged = Object.entries(quality.features || {}).filter(([, stats]) => stats.zero_count === quality.row_count).length;
    const drifted = Object.values(quality.drift || {}).filter((stats) => stats.flagged).length;
    const labelSummary = quality.label_summary?.task === "binary" ? `${quality.label_summary.counts["0"] || 0} negative · ${quality.label_summary.counts["1"] || 0} positive` : `Outcome mean ${number(quality.label_summary?.mean, 2)} · range ${number(quality.label_summary?.min, 2)}–${number(quality.label_summary?.max, 2)}`;
    $("modelLabResult").innerHTML = `<div><span>Cohort quality</span><strong>${quality.row_count} rows · ${quality.feature_count} features</strong><small>${labelSummary} · ${flagged} all-zero feature${flagged === 1 ? "" : "s"}${baseline ? ` · ${drifted} drift flag${drifted === 1 ? "" : "s"} vs ${escapeHtml(baseline.name)}` : ""}</small><code>${escapeHtml(quality.dataset_id)}</code></div>`;
    toast("Cohort quality checked");
  } catch (error) { toast(`Quality check failed · ${error.message}`); }
  button.disabled = false; button.textContent = "Check quality";
}

async function trainModelLab(event) {
  event.preventDefault();
  const labels = {};
  const task = $("modelLabTask").value;
  document.querySelectorAll("[data-lab-label]").forEach((input) => { if (input.value !== "") labels[input.dataset.labLabel] = Number(input.value); });
  const importedDataset = state.modelLabDataset?.task === task ? state.modelLabDataset : null;
  const modelIds = importedDataset ? importedDataset.rows.map((row) => row.model_id) : Object.keys(labels);
  if (modelIds.length < 2) { toast("Choose labels for at least two compiled models"); return; }
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true; button.textContent = "Building…";
  try {
    let dataset = importedDataset;
    if (!dataset) dataset = await api("/api/model-lab/datasets", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ task, name: $("modelLabName").value || "Local clinical baseline", model_ids: modelIds, labels }) });
    state.modelLabDataset = dataset;
    const selectedAlgorithm = $("modelLabAlgorithm").value;
    const searchPayload = { dataset_id: dataset.id, name: $("modelLabName").value || "Local clinical baseline" };
    if (selectedAlgorithm !== "auto") {
      searchPayload.candidates = selectedAlgorithm === "random-forest"
        ? [{ algorithm: "random-forest", n_estimators: 32, max_depth: 6, min_samples_leaf: 1, seed: 17 }]
        : [{ algorithm: selectedAlgorithm, iterations: 600, learning_rate: task === "regression" ? 0.03 : 0.08, l2: 0.001 }];
    }
    const algorithm = await api("/api/model-lab/search", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(searchPayload) });
    const metrics = algorithm.training?.metrics || {};
    const validation = algorithm.training?.validation;
    const evaluated = validation?.metrics || metrics;
    const metricSummary = task === "regression" ? `${algorithm.training?.row_count || 0} training rows · RMSE ${number(metrics.rmse, 2)} · R² ${number(metrics.r2, 2)}${validation ? ` · ${validation.row_count} validation row${validation.row_count === 1 ? "" : "s"} · RMSE ${number(evaluated.rmse, 2)}` : ""}` : `${algorithm.training?.row_count || 0} training rows · ${Math.round((metrics.accuracy || 0) * 100)}% fit accuracy${validation ? ` · ${validation.row_count} validation row${validation.row_count === 1 ? "" : "s"} · ${Math.round((evaluated.accuracy || 0) * 100)}% validation` : ""}`;
    const artifactDatasetId = algorithm.training?.dataset_id || dataset.id;
    $("modelLabResult").innerHTML = `<div><span>Algorithm ready</span><strong>${escapeHtml(algorithm.name)}</strong><small>${metricSummary}</small><code>${escapeHtml(algorithm.id)}</code><div class="model-lab-result-actions"><button class="quiet-action" data-cross-validate="${escapeHtml(algorithm.id)}" data-dataset-id="${escapeHtml(artifactDatasetId)}">Cross-validate cohort</button><button class="quiet-action" data-batch-infer="${escapeHtml(algorithm.id)}" data-dataset-id="${escapeHtml(artifactDatasetId)}">Run cohort inference</button></div></div>`;
    const crossValidateButton = $("modelLabResult").querySelector("[data-cross-validate]");
    crossValidateButton.addEventListener("click", () => crossValidateModel(crossValidateButton.dataset.crossValidate, crossValidateButton.dataset.datasetId, crossValidateButton));
    const batchInferenceButton = $("modelLabResult").querySelector("[data-batch-infer]");
    batchInferenceButton.addEventListener("click", () => batchPredictModel(batchInferenceButton.dataset.batchInfer, batchInferenceButton.dataset.datasetId, batchInferenceButton));
    if (state.model) {
      const prediction = await api(`/api/model-lab/algorithms/${encodeURIComponent(algorithm.id)}/predict`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ model_id: state.model.id }) });
      const predictionLabel = task === "regression" ? number(prediction.prediction, 2) : `${Math.round(prediction.probability_positive * 100)}% positive probability`;
      $("modelLabResult").insertAdjacentHTML("beforeend", `<div class="model-lab-prediction"><span>Current model output</span><strong>${predictionLabel}</strong><small>Top signal: ${escapeHtml(Object.entries(prediction.contributions).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))[0]?.[0] || "none")}</small></div>`);
    }
    toast("Algorithm trained with persisted provenance");
  } catch (error) { toast(`Model lab failed · ${error.message}`); }
  button.disabled = false; button.textContent = "Train and validate";
}

async function exportRepresentation(kind) {
  if (!state.model) return;
  try {
    const safeId = state.model.id.replaceAll(":", "-");
    if (kind === "json") { const payload = await api(`/api/patient-models/${encodeURIComponent(state.model.id)}/export`); downloadJson(payload, `phasemed-${safeId}.json`); }
    if (kind === "graph") { const payload = await api(`/api/graph/${encodeURIComponent(state.model.id)}`); downloadJson(payload, `phasemed-${safeId}-graph.jsonld`); }
    if (kind === "context") { const payload = { model_id: state.model.id, patient_id: state.model.patient_id, selected_object: currentObject(), nearby_objects: currentObject() ? nearestLocal(currentObject().id) : [], context_bindings: state.model.context_bindings || [], temporal_links: state.model.temporal_links || [], capabilities: state.model.capabilities || {} }; downloadJson(payload, `phasemed-${safeId}-context.json`); }
    closeSheet("exportSheet"); toast(`${kind === "graph" ? "Graph" : kind === "context" ? "Context bundle" : "PatientModel"} exported`);
  } catch (error) { toast(`Export failed · ${error.message}`); }
}

async function modelTool(question) { const object = currentObject(); if (!object || !state.model) return "Open a compiled PatientModel and select an object first."; const lower = question.toLowerCase(); let tool = "get_object"; let argumentsValue = { object_id: object.id }; if (lower.includes("closest") || lower.includes("near")) tool = "get_neighbors"; else if (lower.includes("change") || lower.includes("prior") || lower.includes("history")) tool = "get_changes"; else if (lower.includes("evidence") || lower.includes("source") || lower.includes("context")) tool = "get_context"; const result = await api(`/api/patient-models/${encodeURIComponent(state.model.id)}/tools`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ tool, arguments: argumentsValue }) }); if (tool === "get_neighbors") { const first = result.results?.[0]; return first ? `The closest indexed structure is <b>${escapeHtml(first.label)}</b> at <b>${number(first.distance_mm)} mm</b>. This is a deterministic surface-distance query.` : `No neighboring geometry is available for <b>${escapeHtml(object.label)}</b>.`; } if (tool === "get_changes") { const link = result.links?.[0]; return link ? `<b>${escapeHtml(object.label)}</b> has a persisted <b>${escapeHtml(link.type)}</b> link. ${link.changes?.volume_change_percent == null ? "No numeric volume change is available." : `The calculated volume change is <b>${number(link.changes.volume_change_percent)}%</b>.`}` : `No prior link is available for <b>${escapeHtml(object.label)}</b>.`; } if (tool === "get_context") { return result.results?.length ? `<b>${result.results.length}</b> context bindings are attached to <b>${escapeHtml(object.label)}</b>. The source and review state are available in the evidence inspector.` : `No context is bound to <b>${escapeHtml(object.label)}</b>.`; } return `<b>${escapeHtml(object.label)}</b> is a ${escapeHtml(object.type)} object with persisted geometry and provenance.`; }
async function askModel(question) { if (!question.trim()) return; $("aiThread").insertAdjacentHTML("beforeend", `<div class="ai-message user"><p>${escapeHtml(question)}</p></div>`); try { const answer = await modelTool(question); $("aiThread").insertAdjacentHTML("beforeend", `<div class="ai-message"><p>${answer}</p></div>`); } catch (error) { $("aiThread").insertAdjacentHTML("beforeend", `<div class="ai-message"><p>Structured query unavailable: ${escapeHtml(error.message)}</p></div>`); } $("aiThread").scrollTop = $("aiThread").scrollHeight; }

function renderProcedureResult(result) { if (!result) { $("procedureResult").innerHTML = `<span class="result-placeholder">Path results will appear here.</span>`; return; } const clearance = (result.clearance || []).filter((item) => item.clearance_mm != null).sort((a, b) => a.clearance_mm - b.clearance_mm).slice(0, 4); $("procedureResult").innerHTML = `<h3>Proposed path</h3><div class="path-stat"><span>Length</span><strong>${number(result.length_mm)} mm</strong></div><div class="path-stat"><span>Intersections</span><strong>${result.intersections?.length || 0} objects</strong></div>${clearance.map((item) => `<div class="path-stat"><span>${escapeHtml(item.label)}</span><strong>${number(item.clearance_mm)} mm clear</strong></div>`).join("")}<p class="sheet-footnote">Method: ${escapeHtml(result.method || "segment geometry")} · planning visualization only</p>`; }
function activateTool(tool) { state.activeTool = tool; document.querySelectorAll("[data-tool]").forEach((item) => item.classList.toggle("active", item.dataset.tool === tool)); const cursor = tool === "rotate" ? "grab" : tool === "pan" ? "move" : tool === "measure" || tool === "path" ? "crosshair" : tool === "zoom" ? "zoom-in" : "default"; $("sceneCanvas").style.cursor = cursor; $("procedureCanvas").style.cursor = cursor; }
function leavePathMode() { state.pathMode = false; state.pathPoints = []; if (state.activeTool === "path") activateTool("rotate"); }
function startPath() { if (!state.model) { toast("Compile a PatientModel before drawing a path"); return; } state.pathMode = true; state.pathPoints = []; activateTool("path"); setMode("procedure"); toast("Click an entry point and then a target in the path canvas"); drawAll(); }
async function finishPath(first, second, canvas) { leavePathMode(); const start = mapWorld(first, canvas); const end = mapWorld(second, canvas); try { const path = await api(`/api/patient-models/${encodeURIComponent(state.model.id)}/procedure-paths`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ start, end }) }); renderProcedureResult(path.result); state.model.procedure_paths ||= []; state.model.procedure_paths.push(path); toast(`Path ${number(path.result.length_mm)} mm · ${path.result.intersections?.length || 0} intersections`); } catch (error) { toast(`Path query failed · ${error.message}`); } drawAll(); }

async function applyTimeline(value) {
  const nextPrior = state.temporalMode === "prior";
  if (nextPrior && state.priorModel && state.viewModel?.id !== state.priorModel.id) {
    const currentId = state.selectedId;
    state.viewModel = state.priorModel;
    state.selectedId = priorObjectForCurrent((state.model?.objects || []).find((item) => item.id === currentId))?.id || defaultObjectId(state.priorModel);
    await loadVolume(state.priorStudy);
  } else if (!nextPrior && state.model && state.viewModel?.id !== state.model.id) {
    const priorId = state.selectedId;
    state.viewModel = state.model;
    state.selectedId = currentObjectForPrior(priorId)?.id || defaultObjectId(state.model);
    await loadVolume(state.study);
  }
  renderInspector(); drawAll(); updateCrosshair();
}
async function setTemporalMode(mode) {
  if (mode !== "current" && !state.priorModel) { toast("No compatible prior PatientModel is linked"); return; }
  state.temporalMode = mode;
  if (mode === "prior") state.timelineValue = 0;
  else if (mode === "current" || mode === "overlay" || mode === "difference") state.timelineValue = 100;
  $("timelineInput").value = String(state.timelineValue);
  $("timelineFill").style.width = `${state.timelineValue}%`;
  $("timelineSlider").style.setProperty("--pos", `${state.timelineValue}%`);
  $("timelineLabel").textContent = mode === "prior" ? "Prior study" : mode === "morph" ? "Morph view" : mode === "overlay" ? "Overlay" : mode === "difference" ? "Difference" : "Current study";
  await applyTimeline(state.timelineValue);
  renderTimeline(); drawAll();
}
function setMode(mode) { if (mode !== "procedure") leavePathMode(); if (mode !== "model" && state.activeTool === "measure") { state.measurePoints = []; state.measureResult = null; $("measureReadout").classList.add("hidden"); activateTool("rotate"); } state.mode = mode; document.querySelectorAll(".mode-item").forEach((button) => { button.classList.toggle("active", button.dataset.mode === mode); button.setAttribute("aria-selected", button.dataset.mode === mode ? "true" : "false"); }); renderWorkspace(); if (mode === "hologram") { warmCurrentModel(); warmVoiceEngine(); updateHologramControls(); startHoloSpin(); } else { stopVoiceCapture(); stopHoloSpin(); } }

function wireEvents() {
  setTheme(state.theme);
  $("themeButton").addEventListener("click", () => { setTheme(state.theme === "contrast" ? "light" : "contrast"); toast(state.theme === "contrast" ? "High contrast display" : "Standard clinical display"); });
  $("importButton").addEventListener("click", () => $("dicomInput").click()); $("emptyImport").addEventListener("click", () => $("dicomInput").click()); $("newStudy").addEventListener("click", () => $("dicomInput").click()); $("dicomInput").addEventListener("change", (event) => { importFiles(event.target.files); event.target.value = ""; }); $("folderButton").addEventListener("click", () => $("folderInput").click()); $("folderInput").addEventListener("change", (event) => { importFiles(event.target.files); event.target.value = ""; }); $("seriesControl").addEventListener("click", openSeriesChooser);
  $("contextImport").addEventListener("click", () => state.model ? $("contextInput").click() : toast("Compile a PatientModel before adding context")); $("contextInput").addEventListener("change", (event) => importContextFile(event.target.files[0]));
  $("exportButton").addEventListener("click", () => showSheet("exportSheet")); $("historyButton").addEventListener("click", openModelHistory); $("inspectorMenu").addEventListener("click", () => state.model ? showSheet("exportSheet") : toast("Open a PatientModel first"));
  $("exportJson").addEventListener("click", () => exportRepresentation("json")); $("exportGraph").addEventListener("click", () => exportRepresentation("graph")); $("exportContext").addEventListener("click", () => exportRepresentation("context"));
  $("gatewayButton").addEventListener("click", openGateway); $("emptyGateway").addEventListener("click", openGateway); $("modelLabButton").addEventListener("click", openModelLab); $("modelLabForm").addEventListener("submit", trainModelLab); $("inspectQuality").addEventListener("click", inspectModelLabQuality); $("analyzeCohort").addEventListener("click", analyzeModelLabCohort); $("importModelLabels").addEventListener("click", () => $("modelLabLabelFile").click()); $("modelLabLabelFile").addEventListener("change", (event) => { importModelLabels(event.target.files[0]); event.target.value = ""; });
  $("studySearch").addEventListener("input", (event) => { state.search = event.target.value; renderStudies(); }); $("studyFilter").addEventListener("click", () => { state.filterReady = !state.filterReady; $("studyFilter").classList.toggle("active", state.filterReady); renderStudies(); });
  document.querySelectorAll("[data-library-view]").forEach((button) => button.addEventListener("click", () => { document.querySelectorAll("[data-library-view]").forEach((item) => item.classList.toggle("active", item === button)); document.querySelectorAll("[data-rail-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.railPanel === button.dataset.libraryView)); }));
  document.querySelectorAll("[data-analysis-tab]").forEach((button) => button.addEventListener("click", () => { document.querySelectorAll("[data-analysis-tab]").forEach((item) => item.classList.toggle("active", item === button)); document.querySelectorAll("[data-analysis-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.analysisPanel === button.dataset.analysisTab)); }));
  document.querySelectorAll("[data-mobile-panel]").forEach((button) => button.addEventListener("click", () => { const panel = button.dataset.mobilePanel; document.body.dataset.mobilePanel = panel === "workspace" ? "" : panel; document.querySelectorAll("[data-mobile-panel]").forEach((item) => item.classList.toggle("active", item === button)); if (panel === "more") { if (state.model) showSheet("exportSheet"); else showSheet("capabilitySheet"); } }));
  document.querySelectorAll(".mode-item").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
  $("hologramExit").addEventListener("click", () => setMode("model"));
  $("hologramSpin").addEventListener("click", () => { setHoloSpin(!state.holoSpin); toast(state.holoSpin ? "Hologram rotation running" : "Hologram rotation paused"); });
  const voiceButton = $("hologramVoice");
  voiceButton.addEventListener("pointerdown", (event) => { event.preventDefault(); voiceButton.setPointerCapture?.(event.pointerId); startVoiceCapture(); });
  ["pointerup", "pointercancel", "pointerleave"].forEach((type) => voiceButton.addEventListener(type, stopVoiceCapture));
  // The button is focusable, so keep its own space/enter activation from starting a second take.
  voiceButton.addEventListener("keydown", (event) => { if (event.key === " " || event.key === "Enter") event.preventDefault(); });
  $("hologramAnatomy").addEventListener("click", (event) => { const group = cycleAnatomyGroup(event.shiftKey ? -1 : 1); drawHologram(); toast(group ? `${group.label} view` : "All segmented anatomy"); });
  $("hologramZoomIn").addEventListener("click", () => zoomHologram(1.2));
  $("hologramZoomOut").addEventListener("click", () => zoomHologram(.8));
  $("hologramReset").addEventListener("click", () => { resetHologramView(); clearHighlights(); toast("Hologram view reset"); });
  const holoCanvas = $("hologramCanvas");
  holoCanvas.addEventListener("contextmenu", (event) => event.preventDefault());
  holoCanvas.addEventListener("pointerdown", (event) => {
    if (state.mode !== "hologram") return;
    const pan = event.shiftKey || event.button === 1 || event.button === 2;
    state.holoDrag = { kind: pan ? "pan" : "rotate", x: event.clientX, y: event.clientY, yaw: state.holoYaw, pitch: state.holoPitch, panX: state.holoPanX, panY: state.holoPanY, spin: state.holoSpin };
    stopHoloSpin();
    holoCanvas.classList.add(pan ? "panning" : "dragging");
    holoCanvas.setPointerCapture(event.pointerId);
  });
  holoCanvas.addEventListener("pointermove", (event) => {
    if (!state.holoDrag) return;
    const dx = event.clientX - state.holoDrag.x; const dy = event.clientY - state.holoDrag.y;
    if (state.holoDrag.kind === "pan") { state.holoPanX = state.holoDrag.panX + dx; state.holoPanY = state.holoDrag.panY + dy; }
    else { state.holoYaw = state.holoDrag.yaw + dx * .008; state.holoPitch = clamp(state.holoDrag.pitch + dy * .008, -Math.PI / 2, Math.PI / 2); }
    requestHoloDraw();
  });
  const endHoloDrag = () => { if (!state.holoDrag) return; const resume = state.holoDrag.spin; state.holoDrag = null; holoCanvas.classList.remove("dragging", "panning"); if (resume) startHoloSpin(); };
  holoCanvas.addEventListener("pointerup", endHoloDrag);
  holoCanvas.addEventListener("pointercancel", endHoloDrag);
  holoCanvas.addEventListener("wheel", (event) => { if (state.mode !== "hologram") return; event.preventDefault(); zoomHologram(event.deltaY > 0 ? .9 : 1.11); }, { passive: false });
  document.addEventListener("keydown", (event) => {
    if (state.mode !== "hologram" || event.metaKey || event.ctrlKey || ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
    if (["+", "="].includes(event.key)) { event.preventDefault(); zoomHologram(1.2); }
    else if (["-", "_"].includes(event.key)) { event.preventDefault(); zoomHologram(.8); }
    else if (event.key === "0") { event.preventDefault(); resetHologramView(); }
    else if (event.key === " ") { event.preventDefault(); if (!event.repeat) startVoiceCapture(); }
    else if (event.key === "s" || event.key === "S") { event.preventDefault(); setHoloSpin(!state.holoSpin); }
    else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      if (event.shiftKey) { state.holoPanX += event.key === "ArrowLeft" ? -20 : event.key === "ArrowRight" ? 20 : 0; state.holoPanY += event.key === "ArrowUp" ? -20 : event.key === "ArrowDown" ? 20 : 0; }
      else if (event.key === "ArrowLeft" || event.key === "ArrowRight") state.holoYaw += event.key === "ArrowLeft" ? -.08 : .08;
      else state.holoPitch = clamp(state.holoPitch + (event.key === "ArrowUp" ? -.08 : .08), -Math.PI / 2, Math.PI / 2);
      requestHoloDraw();
    }
  });
  document.addEventListener("keyup", (event) => { if (event.key === " " && state.voiceHeld) { event.preventDefault(); stopVoiceCapture(); } });
  window.addEventListener("blur", stopVoiceCapture);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopVoiceCapture(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && state.mode === "hologram") { stopVoiceCapture(); setMode("model"); } });
  document.querySelectorAll("[data-mode-jump]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.modeJump)));
  document.querySelectorAll("[data-tool]").forEach((button) => button.addEventListener("click", () => { const tool = button.dataset.tool; if (tool === "path") { if (state.pathMode) { leavePathMode(); setMode("model"); toast("Trajectory drawing canceled"); } else startPath(); return; } if (tool === "measure" && state.activeTool === "measure") { activateTool("rotate"); toast("Measure tool closed"); return; } if (state.mode === "procedure") setMode("model"); activateTool(tool); toast(`${tool[0].toUpperCase() + tool.slice(1)} tool active`); }));
  document.querySelectorAll("[data-temporal-mode]").forEach((button) => button.addEventListener("click", () => setTemporalMode(button.dataset.temporalMode)));
  $("resetButton").addEventListener("click", () => { state.isolate = false; state.showLinks = false; state.highlights = []; state.voiceTranscript = ""; resetScenePreset(); leavePathMode(); activateTool("rotate"); state.measurePoints = []; state.measureResult = null; state.sceneYaw = CAM_DEFAULT.yaw; state.scenePitch = CAM_DEFAULT.pitch; state.sceneZoom = 1; state.scenePanX = 0; state.scenePanY = 0; state.temporalMode = "current"; state.timelineValue = 100; $("timelineInput").value = "100"; $("timelineFill").style.width = "100%"; $("timelineLabel").textContent = "Current study"; $("measureReadout").classList.add("hidden"); state.viewModel = state.model; loadVolume(state.study); renderTimeline(); drawAll(); renderInspector(); toast("View reset"); });
  $("sceneFit").addEventListener("click", () => { fitScene(); toast("Scene fitted to visible geometry"); }); $("sceneNeighbors").addEventListener("click", () => { state.showLinks = !state.showLinks; drawAll(); renderInspector(); toast(state.showLinks ? "Selected structure relations shown" : "Relations hidden"); }); $("sceneIsolate").addEventListener("click", () => { state.isolate = !state.isolate; fitScene(); renderInspector(); toast(state.isolate ? "Selected structure isolated" : "All visible structures restored"); });
  $("sceneAnatomy").addEventListener("click", (event) => { const group = cycleAnatomyGroup(event.shiftKey ? -1 : 1); toast(group ? `${group.label} view` : "All segmented anatomy"); });
  $("startPath").addEventListener("click", startPath);
  $("clearMeasure").addEventListener("click", () => { state.measurePoints = []; state.measureResult = null; $("measureReadout").classList.add("hidden"); drawAll(); });
  $("sceneCanvas").addEventListener("contextmenu", (event) => event.preventDefault());
  $("sceneCanvas").addEventListener("pointerdown", (event) => { const pan = state.activeTool === "pan" || event.shiftKey || event.button === 1 || event.button === 2; if (!pan && state.activeTool !== "rotate") return; state.drag = { kind: pan ? "pan" : "rotate", x: event.clientX, y: event.clientY, yaw: state.sceneYaw, pitch: state.scenePitch, panX: state.scenePanX, panY: state.scenePanY, moved: false }; event.currentTarget.setPointerCapture(event.pointerId); event.currentTarget.style.cursor = pan ? "move" : "grabbing"; });
  $("sceneCanvas").addEventListener("pointermove", (event) => { if (!state.drag) return; const dx = event.clientX - state.drag.x; const dy = event.clientY - state.drag.y; state.drag.moved ||= Math.hypot(dx, dy) > 3; if (state.drag.kind === "pan") { state.scenePanX = state.drag.panX + dx; state.scenePanY = state.drag.panY + dy; } else { state.sceneYaw = state.drag.yaw + dx * .008; state.scenePitch = clamp(state.drag.pitch + dy * .008, -Math.PI / 2, Math.PI / 2); } requestDraw(); });
  $("sceneCanvas").addEventListener("pointerup", (event) => { if (!state.drag) return; state.suppressClick = state.drag.moved; state.drag = null; event.currentTarget.style.cursor = state.activeTool === "rotate" ? "grab" : state.activeTool === "pan" ? "move" : "default"; });
  $("sceneCanvas").addEventListener("pointercancel", () => { state.drag = null; });
  $("sceneCanvas").addEventListener("wheel", (event) => { if (!state.model) return; event.preventDefault(); zoomScene(event.deltaY > 0 ? .88 : 1.14, pointFromEvent(event, $("sceneCanvas"))); }, { passive: false });
  $("sceneCanvas").addEventListener("click", (event) => { if (!state.model || state.suppressClick) { state.suppressClick = false; return; } const canvas = $("sceneCanvas"); const point = pointFromEvent(event, canvas); point.world = mapWorld(point, canvas); if (state.activeTool === "zoom") { zoomScene(event.shiftKey ? .8 : 1.2, point); return; } if (state.activeTool === "measure") { if (state.measurePoints.length >= 2) state.measurePoints = []; state.measurePoints.push(point); if (state.measurePoints.length === 2) { const start = mapWorld(state.measurePoints[0], canvas); const end = mapWorld(state.measurePoints[1], canvas); state.measureResult = Math.sqrt(start.reduce((sum, value, index) => sum + (value - end[index]) ** 2, 0)); $("measureValue").textContent = `${number(state.measureResult)} mm`; $("measureReadout").classList.remove("hidden"); } drawAll(); return; } if (state.pathMode) { state.pathPoints.push(point); if (state.pathPoints.length === 2) finishPath(state.pathPoints[0], state.pathPoints[1], canvas); drawAll(); return; } const hit = hitTest(point, canvas); if (hit) selectObject(hit.id); });
  $("sceneCanvas").tabIndex = 0; $("sceneCanvas").addEventListener("keydown", (event) => { if (["+", "="].includes(event.key)) { event.preventDefault(); zoomScene(1.2); } else if (["-", "_"].includes(event.key)) { event.preventDefault(); zoomScene(.8); } else if (event.key === "0") { event.preventDefault(); fitScene(); } else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) { event.preventDefault(); state.scenePanX += event.key === "ArrowLeft" ? -20 : event.key === "ArrowRight" ? 20 : 0; state.scenePanY += event.key === "ArrowUp" ? -20 : event.key === "ArrowDown" ? 20 : 0; drawAll(); } });
  $("procedureCanvas").addEventListener("click", (event) => { if (!state.pathMode) return; const canvas = $("procedureCanvas"); const point = pointFromEvent(event, canvas); point.world = mapWorld(point, canvas); state.pathPoints.push(point); if (state.pathPoints.length === 2) finishPath(state.pathPoints[0], state.pathPoints[1], canvas); drawAll(); });
  $("sliceInput").addEventListener("input", (event) => { state.slice = Number(event.target.value); scheduleSliceRefresh(["axial"]); }); $("windowCenterInput").addEventListener("input", (event) => { state.windowCenter = Number(event.target.value); scheduleSliceRefresh(["axial", "coronal", "sagittal"], 100); }); $("windowWidthInput").addEventListener("input", (event) => { state.windowWidth = Math.max(1, Number(event.target.value)); scheduleSliceRefresh(["axial", "coronal", "sagittal"], 100); }); ["axialImage", "coronalImage", "sagittalImage"].forEach((id) => $(id).addEventListener("click", (event) => selectImageObject(event, id.replace("Image", "").toLowerCase()))); const changeTimeline = (value) => { state.timelineValue = Number(value); if (!state.priorModel) { state.temporalMode = "current"; state.timelineValue = 100; } else if (["current", "prior"].includes(state.temporalMode)) state.temporalMode = state.timelineValue < 50 ? "prior" : "current"; $("timelineInput").value = String(state.timelineValue); $("timelineFill").style.width = `${state.timelineValue}%`; $("timelineSlider").style.setProperty("--pos", `${state.timelineValue}%`); $("timelineLabel").textContent = state.temporalMode === "morph" ? "Morph view" : state.temporalMode === "prior" ? "Prior study" : state.temporalMode === "overlay" ? "Overlay" : state.temporalMode === "difference" ? "Difference" : "Current study"; applyTimeline(state.timelineValue); renderTimeline(); }; $("timelineInput").addEventListener("input", (event) => changeTimeline(event.target.value));
  $("timelinePlay").addEventListener("click", () => { if (!state.priorModel) { toast("No compatible prior model is linked"); return; } if (state.timelineTimer) { clearInterval(state.timelineTimer); state.timelineTimer = null; $("timelinePlay").textContent = "▶"; return; } state.temporalMode = "morph"; state.timelineValue = 0; changeTimeline(0); $("timelinePlay").textContent = "Ⅱ"; state.timelineTimer = setInterval(() => { const next = state.timelineValue + 2; changeTimeline(next); $("timelineInput").value = String(next); if (next >= 100) { clearInterval(state.timelineTimer); state.timelineTimer = null; $("timelinePlay").textContent = "▶"; } }, 45); });
  document.querySelectorAll("[data-expand-view]").forEach((button) => button.addEventListener("click", () => { const viewport = button.closest(".dicom-viewport"); const expanded = viewport.classList.toggle("expanded"); document.querySelectorAll(".dicom-viewport").forEach((item) => { if (item !== viewport) item.classList.toggle("suppressed", expanded); }); button.textContent = expanded ? "×" : "⛶"; }));
  ["capabilityInfoLibrary", "capabilityInfoInspector"].forEach((id) => $(id).addEventListener("click", () => showSheet("capabilitySheet"))); $("contextAskModel").addEventListener("click", () => showSheet("aiSheet")); $("contextSearchForm").addEventListener("submit", async (event) => { event.preventDefault(); if (!state.model) return; state.contextQuery = $("contextQueryInput").value.trim(); try { const result = await api(`/api/patient-models/${encodeURIComponent(state.model.id)}/context-query`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ query: state.contextQuery }) }); renderContextView(result.results); } catch (error) { toast(`Context search failed · ${error.message}`); } }); $("aiForm").addEventListener("submit", (event) => { event.preventDefault(); const question = $("aiInput").value; $("aiInput").value = ""; askModel(question); }); $("aiThread").addEventListener("click", () => {}); document.querySelectorAll("[data-query]").forEach((button) => button.addEventListener("click", () => askModel(button.dataset.query)));
  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => closeSheet(button.dataset.close))); $("scrim").addEventListener("click", closeAllSheets);
  $("objectSearch").addEventListener("input", (event) => { state.objectSearch = event.target.value; renderObjectBrowser(); });
  window.addEventListener("resize", drawAll);
}

wireEvents(); renderWorkspace(); loadStudies().then(() => whenIdle(prefetchStudies));
