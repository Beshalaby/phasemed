const $ = (id) => document.getElementById(id);
let latest = null;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = {};
  try { body = text ? JSON.parse(text) : {}; } catch { body = { detail: text }; }
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

// Problems are shown under the form, where the person is looking, not in a blocking dialog.
function showMessage(text = "") { const message = $("formMessage"); message.textContent = text; message.hidden = !text; }
function setStatus(ok, text = "") {
  const status = $("apiStatus");
  status.classList.toggle("ready", ok);
  status.title = text || (ok ? "Local API connected" : "Local API unavailable");
}

function number(id) { return Number($(id).value); }

function payload() {
  return {
    title: $("title").value,
    indication: $("indication").value,
    phase: $("phase").value,
    endpoint: $("endpoint").value,
    control_label: $("controlLabel").value,
    treatment_label: $("treatmentLabel").value,
    control_rate: number("controlRate"),
    treatment_rate: number("treatmentRate"),
    control_mean: number("controlMean"),
    treatment_mean: number("treatmentMean"),
    standard_deviation: number("standardDeviation"),
    hazard_ratio: number("hazardRatio"),
    event_rate: number("eventRate"),
    alpha: number("alpha"),
    target_power: number("targetPower"),
    dropout_rate: number("dropoutRate"),
    allocation_ratio: number("allocationRatio"),
    interim_fraction: number("interimFraction"),
    simulations: number("simulations"),
    seed: number("seed"),
  };
}

function setEndpointFields() {
  const endpoint = $("endpoint").value;
  $("binaryFields").classList.toggle("hidden", endpoint !== "binary");
  $("continuousFields").classList.toggle("hidden", endpoint !== "continuous");
  $("survivalFields").classList.toggle("hidden", endpoint !== "time_to_event");
}

function percent(value, digits = 1) { return `${(Number(value) * 100).toFixed(digits)}%`; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])); }

function renderResult(result) {
  latest = result;
  const { design, planning, operating_characteristics: op, synthetic_cohort: cohort } = result;
  $("resultEmpty").classList.add("hidden");
  $("resultContent").classList.remove("hidden");
  $("resultTitle").textContent = design.title;
  $("resultSubtitle").textContent = `${design.indication} · ${design.phase} · ${design.control_label} vs ${design.treatment_label}`;
  $("metricEnrolled").textContent = planning.expected_enrolled.toLocaleString();
  $("metricPower").textContent = percent(op.achieved_power);
  $("metricPowerDetail").textContent = `target ${percent(design.target_power, 0)}`;
  $("metricAnalyzable").textContent = planning.total_analyzable.toLocaleString();
  $("metricFormula").textContent = `${planning.target_n_control} + ${planning.target_n_treatment} observed target`;
  $("metricInterim").textContent = percent(op.interim_rejection_probability);
  $("powerInterval").textContent = `${percent(op.power_ci_95[0])} – ${percent(op.power_ci_95[1])}`;
  $("medianP").textContent = op.median_p_value.toFixed(3);
  $("meanObserved").textContent = op.mean_observed.toLocaleString();
  $("meanEffect").textContent = Number(op.mean_effect).toFixed(3);
  $("metricFormula").title = planning.formula;
  $("powerBar").style.width = `${Math.max(0, Math.min(100, op.achieved_power * 100))}%`;
  $("powerTarget").style.left = `${Math.max(0, Math.min(100, design.target_power * 100))}%`;
  $("powerTargetLabel").textContent = `Target ${percent(design.target_power, 0)}`;
  const meets = op.achieved_power >= design.target_power;
  $("powerBadge").textContent = meets ? "ON TARGET" : "BELOW TARGET";
  $("powerBadge").classList.toggle("good", meets);
  $("powerBadge").classList.toggle("caution", !meets);
  $("runId").textContent = result.run_id;
  $("reportText").textContent = result.report;
  $("cohortBody").innerHTML = (cohort.rows || []).map((row) => `<tr><td>${escapeHtml(row.participant_id)}</td><td><span class="arm-chip">${escapeHtml(row.arm)}</span></td><td><span class="status-chip ${row.status === "observed" ? "observed" : "dropout"}">${escapeHtml(row.status)}</span></td><td>${escapeHtml(row.outcome_label)}</td></tr>`).join("");
  $("cohortNote").textContent = `Showing ${cohort.rows_returned} of ${cohort.rows_total} synthetic participants. ${planning.expected_enrolled - planning.total_analyzable} additional participants are the modeled attrition buffer.`;
  $("warnings").innerHTML = (result.warnings || []).map((warning) => `<span>${escapeHtml(warning)}</span>`).join("");
}

async function runSimulation(event) {
  event?.preventDefault();
  const button = document.querySelector(".primary-button");
  button.disabled = true;
  button.innerHTML = "Simulating <span class=\"spinner\">◌</span>";
  try {
    const result = await api("/api/trial-studio/simulate", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload()) });
    renderResult(result);
    setStatus(true);
    showMessage();
    document.querySelector(".results-column").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setStatus(false, error.message);
    showMessage(`The simulation could not run · ${error.message}`);
  } finally {
    button.disabled = false;
    button.innerHTML = "Run simulation <span>↗</span>";
  }
}

function loadDemo() {
  $("title").value = "RESPONSE-01 · Early efficacy";
  $("indication").value = "Metastatic solid tumor";
  $("phase").value = "Phase 2";
  $("endpoint").value = "binary";
  $("controlLabel").value = "Standard of care";
  $("treatmentLabel").value = "REGN-4018";
  $("controlRate").value = "0.30";
  $("treatmentRate").value = "0.48";
  $("alpha").value = "0.05";
  $("targetPower").value = "0.80";
  $("dropoutRate").value = "0.10";
  $("allocationRatio").value = "1";
  $("interimFraction").value = "0.50";
  $("simulations").value = "5000";
  $("seed").value = "20260919";
  setEndpointFields();
  runSimulation();
}

function download(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url);
}

function cohortCsv() {
  if (!latest) return;
  const rows = latest.synthetic_cohort.rows || [];
  const keys = ["participant_id", "arm", "status", "outcome", "outcome_label", "event"];
  const escape = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  download(`${latest.run_id}-cohort.csv`, [keys.join(","), ...rows.map((row) => keys.map((key) => escape(row[key])).join(","))].join("\n"), "text/csv");
}

async function randomizePreview() {
  if (!latest) return;
  try {
    const rows = latest.synthetic_cohort.rows || [];
    const assignments = await api("/api/trial-studio/randomize", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ participant_ids: rows.map((row) => row.participant_id), allocation_ratio: latest.design.allocation_ratio, seed: latest.design.seed, control_label: latest.design.control_label, treatment_label: latest.design.treatment_label }) });
    const byId = new Map(assignments.assignments.map((item) => [item.participant_id, item.arm]));
    rows.forEach((row) => { row.arm = byId.get(row.participant_id) || row.arm; });
    renderResult(latest);
  } catch (error) { showMessage(`Randomization could not run · ${error.message}`); }
}

$("endpoint").addEventListener("change", setEndpointFields);
$("designForm").addEventListener("submit", runSimulation);
$("demoButton").addEventListener("click", loadDemo);
$("downloadJson").addEventListener("click", () => latest && download(`${latest.run_id}.json`, JSON.stringify(latest, null, 2), "application/json"));
$("downloadCsv").addEventListener("click", cohortCsv);
$("randomizeButton").addEventListener("click", randomizePreview);
$("copyReport").addEventListener("click", async () => { if (!latest) return; try { await navigator.clipboard.writeText(latest.report); $("copyReport").textContent = "Copied"; setTimeout(() => { $("copyReport").textContent = "Copy report"; }, 1200); } catch { alert(latest.report); } });

setEndpointFields();
api("/api/health").then(() => setStatus(true)).catch((error) => setStatus(false, error.message));
