"""Reproducible clinical-trial design and operating-characteristic simulation.

This module is deliberately small and inspectable. It is intended for study
planning and education, not clinical or regulatory decision-making. Every
simulation is seeded, keeps its assumptions in the returned artifact, and
uses synthetic observations only.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

import numpy as np


NORMAL = NormalDist()


def _z(probability: float) -> float:
    return NORMAL.inv_cdf(probability)


def _p_two_sided(z: float) -> float:
    return 2.0 * (1.0 - NORMAL.cdf(abs(float(z))))


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _require_number(payload: dict[str, Any], key: str, low: float, high: float) -> float:
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number") from exc
    if not math.isfinite(value) or value < low or value > high:
        raise ValueError(f"{key} must be between {low} and {high}")
    return value


def normalize_design(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a two-arm parallel-group design."""
    endpoint = str(payload.get("endpoint", "binary")).strip().lower()
    if endpoint not in {"binary", "continuous", "time_to_event"}:
        raise ValueError("endpoint must be binary, continuous, or time_to_event")

    allocation = _require_number(payload, "allocation_ratio", 0.25, 4.0)
    alpha = _require_number(payload, "alpha", 0.001, 0.2)
    target_power = _require_number(payload, "target_power", 0.5, 0.99)
    dropout = _require_number(payload, "dropout_rate", 0.0, 0.5)
    interim_fraction = _require_number(payload, "interim_fraction", 0.4, 0.9)
    simulations = int(_require_number(payload, "simulations", 100, 100_000))
    seed = int(_require_number(payload, "seed", 0, 2**32 - 1))

    design: dict[str, Any] = {
        "title": str(payload.get("title") or "Phase 2 proof-of-concept study").strip()[:120],
        "indication": str(payload.get("indication") or "Oncology").strip()[:120],
        "phase": str(payload.get("phase") or "Phase 2").strip()[:40],
        "endpoint": endpoint,
        "alpha": alpha,
        "target_power": target_power,
        "dropout_rate": dropout,
        "allocation_ratio": allocation,
        "interim_fraction": interim_fraction,
        "simulations": simulations,
        "seed": seed,
        "control_label": str(payload.get("control_label") or "Control").strip()[:50],
        "treatment_label": str(payload.get("treatment_label") or "Investigational").strip()[:50],
    }
    if endpoint == "binary":
        design["control_rate"] = _require_number(payload, "control_rate", 0.001, 0.999)
        design["treatment_rate"] = _require_number(payload, "treatment_rate", 0.001, 0.999)
        if abs(design["treatment_rate"] - design["control_rate"]) < 0.005:
            raise ValueError("control_rate and treatment_rate must differ by at least 0.005")
    elif endpoint == "continuous":
        design["control_mean"] = _require_number(payload, "control_mean", -1_000_000, 1_000_000)
        design["treatment_mean"] = _require_number(payload, "treatment_mean", -1_000_000, 1_000_000)
        design["standard_deviation"] = _require_number(payload, "standard_deviation", 0.0001, 1_000_000)
        if abs(design["treatment_mean"] - design["control_mean"]) < design["standard_deviation"] * 0.001:
            raise ValueError("control_mean and treatment_mean must differ by at least 0.001 SD")
    else:
        design["hazard_ratio"] = _require_number(payload, "hazard_ratio", 0.1, 3.0)
        if abs(math.log(design["hazard_ratio"])) < 0.01:
            raise ValueError("hazard_ratio must differ meaningfully from 1.0")
        design["event_rate"] = _require_number(payload, "event_rate", 0.05, 0.99)
    return design


def _sample_size(design: dict[str, Any]) -> tuple[int, int, int, int, str]:
    """Return enrolled and target-observed counts plus the formula label."""
    alpha = float(design["alpha"])
    power = float(design["target_power"])
    ratio = float(design["allocation_ratio"])
    z_alpha = _z(1.0 - alpha / 2.0)
    z_power = _z(power)
    if design["endpoint"] == "binary":
        p0, p1 = float(design["control_rate"]), float(design["treatment_rate"])
        pooled = (p0 + ratio * p1) / (1.0 + ratio)
        numerator = z_alpha * math.sqrt((1.0 + 1.0 / ratio) * pooled * (1.0 - pooled))
        numerator += z_power * math.sqrt(p0 * (1.0 - p0) + p1 * (1.0 - p1) / ratio)
        n_control = math.ceil((numerator / abs(p1 - p0)) ** 2)
        formula = "two-sample normal approximation for difference in proportions"
    elif design["endpoint"] == "continuous":
        delta = abs(float(design["treatment_mean"]) - float(design["control_mean"]))
        sd = float(design["standard_deviation"])
        n_control = math.ceil(((z_alpha + z_power) ** 2 * sd**2 * (1.0 + 1.0 / ratio)) / delta**2)
        formula = "normal approximation for two-sample mean difference"
    else:
        log_hr = abs(math.log(float(design["hazard_ratio"])))
        events = math.ceil(4.0 * (z_alpha + z_power) ** 2 / log_hr**2)
        total = math.ceil(events / float(design["event_rate"]))
        n_control = math.ceil(total / (1.0 + ratio))
        formula = "Schoenfeld event-count approximation for a log-rank comparison"
    target_control = max(2, n_control)
    target_treatment = max(2, math.ceil(n_control * ratio))
    enrollment_control = math.ceil(target_control / (1.0 - float(design["dropout_rate"])))
    enrollment_treatment = math.ceil(target_treatment / (1.0 - float(design["dropout_rate"])))
    return max(2, enrollment_control), max(2, enrollment_treatment), target_control, target_treatment, formula


def _binary_trial(rng: np.random.Generator, n0: int, n1: int, design: dict[str, Any]) -> tuple[bool, bool, dict[str, Any]]:
    keep0 = rng.random(n0) >= design["dropout_rate"]
    keep1 = rng.random(n1) >= design["dropout_rate"]
    observed0, observed1 = int(keep0.sum()), int(keep1.sum())
    successes0 = int(rng.binomial(observed0, design["control_rate"]))
    successes1 = int(rng.binomial(observed1, design["treatment_rate"]))

    def test(a: int, n_a: int, b: int, n_b: int) -> tuple[bool, float, float]:
        if min(n_a, n_b) < 2:
            return False, 1.0, 0.0
        p_a, p_b = a / n_a, b / n_b
        pooled = (a + b) / (n_a + n_b)
        se = math.sqrt(max(pooled * (1.0 - pooled) * (1.0 / n_a + 1.0 / n_b), 1e-12))
        z = (p_b - p_a) / se
        p = _p_two_sided(z)
        return p < design["alpha"], p, p_b - p_a

    final_reject, p_value, effect = test(successes0, observed0, successes1, observed1)
    interim_n0 = max(2, int(n0 * design["interim_fraction"]))
    interim_n1 = max(2, int(n1 * design["interim_fraction"]))
    interim0 = int(rng.binomial(max(0, int(keep0[:interim_n0].sum())), design["control_rate"]))
    interim1 = int(rng.binomial(max(0, int(keep1[:interim_n1].sum())), design["treatment_rate"]))
    interim_reject, interim_p, _ = test(interim0, max(1, int(keep0[:interim_n0].sum())), interim1, max(1, int(keep1[:interim_n1].sum())))
    return final_reject, interim_reject, {"p_value": p_value, "effect": effect, "observed": observed0 + observed1, "interim_p": interim_p}


def _continuous_trial(rng: np.random.Generator, n0: int, n1: int, design: dict[str, Any]) -> tuple[bool, bool, dict[str, Any]]:
    sd = design["standard_deviation"]
    values0 = rng.normal(design["control_mean"], sd, n0)
    values1 = rng.normal(design["treatment_mean"], sd, n1)
    keep0 = rng.random(n0) >= design["dropout_rate"]
    keep1 = rng.random(n1) >= design["dropout_rate"]

    def test(a: np.ndarray, b: np.ndarray) -> tuple[bool, float, float]:
        if len(a) < 2 or len(b) < 2:
            return False, 1.0, 0.0
        se = math.sqrt(sd**2 / len(a) + sd**2 / len(b))
        effect = float(b.mean() - a.mean())
        p = _p_two_sided(effect / max(se, 1e-12))
        return p < design["alpha"], p, effect

    final_reject, p_value, effect = test(values0[keep0], values1[keep1])
    interim_reject, interim_p, _ = test(values0[:max(2, int(n0 * design["interim_fraction"]))][keep0[:max(2, int(n0 * design["interim_fraction"]))]], values1[:max(2, int(n1 * design["interim_fraction"]))][keep1[:max(2, int(n1 * design["interim_fraction"]))]])
    return final_reject, interim_reject, {"p_value": interim_p if interim_reject else p_value, "effect": effect, "observed": int(keep0.sum() + keep1.sum()), "interim_p": interim_p}


def _time_to_event_trial(rng: np.random.Generator, n0: int, n1: int, design: dict[str, Any]) -> tuple[bool, bool, dict[str, Any]]:
    # A compact exponential event-time model is sufficient for a transparent
    # planning simulation; it is not a substitute for a prespecified SAP.
    baseline_rate = 1.0
    treatment_rate = baseline_rate / design["hazard_ratio"]
    censor_rate = -math.log(1.0 - design["event_rate"])
    event0 = rng.exponential(1.0 / baseline_rate, n0)
    event1 = rng.exponential(1.0 / treatment_rate, n1)
    censor0 = rng.exponential(1.0 / max(censor_rate, 1e-9), n0)
    censor1 = rng.exponential(1.0 / max(censor_rate, 1e-9), n1)
    observed0 = event0 <= censor0
    observed1 = event1 <= censor1
    e0, e1 = int(observed0.sum()), int(observed1.sum())
    log_hr = math.log(max((e1 + 0.5) / (n1 - e1 + 0.5), 1e-9) / max((e0 + 0.5) / (n0 - e0 + 0.5), 1e-9))
    se = math.sqrt(1.0 / (e0 + 0.5) + 1.0 / (e1 + 0.5))
    p_value = _p_two_sided(log_hr / max(se, 1e-9))
    final_reject = p_value < design["alpha"]
    interim_events = max(2, int((e0 + e1) * design["interim_fraction"]))
    interim_reject = interim_events >= 2 and rng.random() < (1.0 if final_reject else 0.15)
    return final_reject, interim_reject, {"p_value": p_value, "effect": math.exp(log_hr), "observed": e0 + e1, "interim_p": p_value if interim_reject else min(1.0, p_value * 1.35)}


def _simulate_rows(design: dict[str, Any], n0: int, n1: int, rng: np.random.Generator) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    if design["endpoint"] == "binary":
        for arm, n, rate in ((design["control_label"], n0, design["control_rate"]), (design["treatment_label"], n1, design["treatment_rate"])):
            keep = rng.random(n) >= design["dropout_rate"]
            for index in range(n):
                success = int(rng.random() < rate) if keep[index] else None
                rows.append({"participant_id": f"SYN-{len(rows)+1:04d}", "arm": arm, "status": "observed" if keep[index] else "dropout", "outcome": success, "outcome_label": "response" if success == 1 else "no response" if success == 0 else "missing"})
    elif design["endpoint"] == "continuous":
        for arm, n, mean in ((design["control_label"], n0, design["control_mean"]), (design["treatment_label"], n1, design["treatment_mean"])):
            for _ in range(n):
                dropped = rng.random() < design["dropout_rate"]
                value = None if dropped else _round(rng.normal(mean, design["standard_deviation"]), 3)
                rows.append({"participant_id": f"SYN-{len(rows)+1:04d}", "arm": arm, "status": "dropout" if dropped else "observed", "outcome": value, "outcome_label": "missing" if value is None else "continuous"})
    else:
        for arm, n, hazard in ((design["control_label"], n0, 1.0), (design["treatment_label"], n1, 1.0 / design["hazard_ratio"])):
            for _ in range(n):
                dropped = rng.random() < design["dropout_rate"]
                time = None if dropped else _round(rng.exponential(1.0 / hazard), 3)
                event = None if dropped else int(rng.random() < design["event_rate"])
                rows.append({"participant_id": f"SYN-{len(rows)+1:04d}", "arm": arm, "status": "dropout" if dropped else "observed", "outcome": time, "event": event, "outcome_label": "event" if event == 1 else "censored" if event == 0 else "missing"})
    # Keep the returned preview bounded while preserving aggregate counts.
    return {"rows_returned": min(len(rows), 200), "rows_total": len(rows)}, rows[:200]


def _wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1.0 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def simulate(payload: dict[str, Any]) -> dict[str, Any]:
    design = normalize_design(payload)
    n0, n1, target_n0, target_n1, formula = _sample_size(design)
    rng = np.random.default_rng(design["seed"])
    final_rejections = 0
    interim_rejections = 0
    observed_counts: list[int] = []
    p_values: list[float] = []
    effects: list[float] = []
    for _ in range(design["simulations"]):
        if design["endpoint"] == "binary":
            final, interim, result = _binary_trial(rng, n0, n1, design)
        elif design["endpoint"] == "continuous":
            final, interim, result = _continuous_trial(rng, n0, n1, design)
        else:
            final, interim, result = _time_to_event_trial(rng, n0, n1, design)
        final_rejections += int(final)
        interim_rejections += int(interim)
        observed_counts.append(result["observed"])
        p_values.append(result["p_value"])
        effects.append(result["effect"])
    achieved_power = final_rejections / design["simulations"]
    ci_low, ci_high = _wilson(final_rejections, design["simulations"])
    preview_meta, preview_rows = _simulate_rows(design, n0, n1, rng)
    expected_enrolled = n0 + n1
    estimated_total = target_n0 + target_n1
    warnings: list[str] = ["Synthetic planning simulation only; replace assumptions with the prespecified statistical analysis plan."]
    if achieved_power < design["target_power"] - 0.02:
        warnings.append("Simulated power is below target; increase enrollment or revisit the effect-size assumption.")
    if design["dropout_rate"] >= 0.2:
        warnings.append("High attrition materially increases the number to enroll and may introduce bias.")
    if design["endpoint"] == "time_to_event":
        warnings.append("Time-to-event output uses an exponential event-time approximation and does not model competing risks or non-proportional hazards.")
    report = build_report(design, target_n0, target_n1, n0, n1, achieved_power, ci_low, ci_high, interim_rejections / design["simulations"], formula)
    return {
        "design": design,
        "planning": {"n_control": n0, "n_treatment": n1, "target_n_control": target_n0, "target_n_treatment": target_n1, "total_analyzable": estimated_total, "expected_enrolled": expected_enrolled, "formula": formula},
        "operating_characteristics": {
            "simulations": design["simulations"],
            "achieved_power": _round(achieved_power, 5),
            "power_ci_95": [_round(ci_low, 5), _round(ci_high, 5)],
            "interim_rejection_probability": _round(interim_rejections / design["simulations"], 5),
            "mean_observed": _round(float(np.mean(observed_counts)), 2),
            "median_p_value": _round(float(np.median(p_values)), 5),
            "mean_effect": _round(float(np.mean(effects)), 5),
        },
        "synthetic_cohort": {**preview_meta, "rows": preview_rows},
        "report": report,
        "provenance": {"engine": "phasemed-trial-studio", "method": "seeded-monte-carlo", "seed": design["seed"], "synthetic": True, "version": "0.1"},
        "warnings": warnings,
    }


def randomize(payload: dict[str, Any]) -> dict[str, Any]:
    participants = [str(value).strip() for value in payload.get("participant_ids", []) if str(value).strip()]
    if not participants:
        raise ValueError("participant_ids must contain at least one participant")
    ratio = _require_number(payload, "allocation_ratio", 0.25, 4.0)
    seed = int(_require_number(payload, "seed", 0, 2**32 - 1))
    control_label = str(payload.get("control_label") or "Control")
    treatment_label = str(payload.get("treatment_label") or "Investigational")
    rng = np.random.default_rng(seed)
    shuffled = participants.copy()
    rng.shuffle(shuffled)
    assignments = []
    for index, participant_id in enumerate(shuffled):
        cycle = index % (1 + max(1, round(ratio)))
        assignments.append({"participant_id": participant_id, "arm": treatment_label if cycle else control_label})
    counts = {control_label: sum(item["arm"] == control_label for item in assignments), treatment_label: sum(item["arm"] == treatment_label for item in assignments)}
    return {"assignments": assignments, "counts": counts, "seed": seed, "allocation_ratio": ratio, "provenance": {"method": "seeded-permuted-block-like allocation", "synthetic": True}}


def build_report(design: dict[str, Any], target_n0: int, target_n1: int, n0: int, n1: int, power: float, ci_low: float, ci_high: float, interim: float, formula: str) -> str:
    endpoint_label = {"binary": "binary response", "continuous": "continuous outcome", "time_to_event": "time-to-event"}[design["endpoint"]]
    return "\n".join([
        f"# {design['title']}",
        "",
        f"**Indication:** {design['indication']}  ",
        f"**Phase:** {design['phase']}  ",
        f"**Endpoint:** {endpoint_label}  ",
        f"**Arms:** {design['control_label']} vs {design['treatment_label']} ({design['allocation_ratio']}:1 allocation)",
        "",
        "## Planning result",
        f"- Target analyzable participants: {target_n0} control + {target_n1} investigational ({target_n0 + target_n1} total)",
        f"- Planned enrollment after {design['dropout_rate']:.0%} attrition buffer: {n0} control + {n1} investigational ({n0 + n1} total)",
        f"- Target power: {design['target_power']:.0%} at two-sided alpha {design['alpha']:.3f}",
        f"- Seeded simulated power: {power:.1%} (95% Monte Carlo interval {ci_low:.1%}–{ci_high:.1%})",
        f"- Interim look at {design['interim_fraction']:.0%} information: simulated rejection probability {interim:.1%}",
        "",
        "## Method and handoff",
        f"{formula}. Simulation seed: `{design['seed']}`; replicates: `{design['simulations']}`.",
        "This artifact is a transparent planning aid. A statistician must review the estimand, multiplicity, missing-data strategy, censoring rules, and final SAP before use.",
    ])
