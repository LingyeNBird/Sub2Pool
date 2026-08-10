from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .models import AlgorithmOutput, Observations, SimulationTruth
from .observation import true_limits


def applied_series(sample_values: np.ndarray, sample_idx: np.ndarray, fine_length: int) -> np.ndarray:
    fine_idx = np.arange(fine_length)
    pos = np.searchsorted(sample_idx, fine_idx, side="right") - 1
    pos = np.clip(pos, 0, len(sample_idx) - 1)
    return sample_values[pos]


def evaluate_output(
    truth: SimulationTruth,
    obs: Observations,
    output: AlgorithmOutput,
) -> dict[str, Any]:
    b_true, l_true = true_limits(truth)
    b_applied = applied_series(output.b_hat, obs.sample_idx, len(truth.t))
    l_applied = applied_series(output.l_hat, obs.sample_idx, len(truth.t))
    e = b_applied - b_true
    e_l = l_applied - l_true
    abs_e = np.abs(e)
    participant_mae = abs_e.mean(axis=0)
    initial_scale = max(float(np.mean(truth.rights * 1750.0)), 1e-9)

    delta_b = np.diff(output.b_hat, axis=0)
    adjustment_magnitude = float(np.abs(delta_b).sum() / truth.spec.n_participants)
    adjustment_count = float((np.abs(delta_b) > 1.0).sum() / truth.spec.n_participants)

    sample_error = output.b_hat - b_true[obs.sample_idx]
    sample_abs = np.abs(sample_error)
    metrics: dict[str, Any] = {
        "case_id": truth.spec.case_id,
        "algorithm": output.algorithm,
        "seed": truth.spec.seed,
        "speed": truth.spec.speed,
        "n_participants": truth.spec.n_participants,
        "rights_profile": truth.spec.rights_profile,
        "scenario": truth.spec.scenario,
        "sample_hours": truth.spec.sample_hours,
        "quantizer": truth.spec.quantizer,
        "mae_usd": float(abs_e.mean()),
        "rmse_usd": float(np.sqrt(np.mean(e * e))),
        "bias_usd": float(e.mean()),
        "p95_abs_usd": float(np.quantile(abs_e, 0.95)),
        "p99_abs_usd": float(np.quantile(abs_e, 0.99)),
        "max_abs_usd": float(abs_e.max()),
        "mean_over_usd": float(np.maximum(e, 0.0).mean()),
        "p95_over_usd": float(np.quantile(np.maximum(e, 0.0), 0.95)),
        "max_over_usd": float(np.maximum(e, 0.0).max()),
        "mean_under_usd": float(np.maximum(-e, 0.0).mean()),
        "over_duration_fraction": float((e > 0.0).mean()),
        "under_duration_fraction": float((e < 0.0).mean()),
        "worst_participant_mae_usd": float(participant_mae.max()),
        "participant_mae_spread_usd": float(participant_mae.max() - participant_mae.min()),
        "normalized_mae": float(abs_e.mean() / initial_scale),
        "sample_instant_mae_usd": float(sample_abs.mean()),
        "hold_mae_increment_usd": float(abs_e.mean() - sample_abs.mean()),
        "adjustment_total_variation_usd": adjustment_magnitude,
        "adjustment_count_gt_1usd": adjustment_count,
        "l_mae_usd": float(np.abs(e_l).mean()),
        "final_total_progress_pp": float(truth.p[-1]),
        "total_dollars": float(truth.c[-1].sum()),
    }
    if output.q_hat is not None:
        q_err = output.q_hat - truth.q[obs.sample_idx]
        metrics.update(
            q_sample_mae_pp=float(np.abs(q_err).mean()),
            q_final_mae_pp=float(np.abs(q_err[-1]).mean()),
            q_final_max_abs_pp=float(np.abs(q_err[-1]).max()),
        )
    else:
        metrics.update(q_sample_mae_pp=np.nan, q_final_mae_pp=np.nan, q_final_max_abs_pp=np.nan)
    if output.v_hat is not None:
        v_err = output.v_hat - truth.v[obs.sample_idx]
        metrics.update(
            v_sample_mae_usd=float(np.abs(v_err).mean()),
            v_sample_p95_abs_usd=float(np.quantile(np.abs(v_err), 0.95)),
        )
    else:
        metrics.update(v_sample_mae_usd=np.nan, v_sample_p95_abs_usd=np.nan)

    if output.b_lower is not None and output.b_upper is not None:
        sample_truth = b_true[obs.sample_idx]
        covered = (sample_truth >= output.b_lower - 1e-9) & (sample_truth <= output.b_upper + 1e-9)
        widths = output.b_upper - output.b_lower
        metrics.update(
            interval_sample_coverage=float(covered.mean()),
            interval_mean_width_usd=float(widths.mean()),
            interval_p95_width_usd=float(np.quantile(widths, 0.95)),
        )
        lower_applied = applied_series(output.b_lower, obs.sample_idx, len(truth.t))
        upper_applied = applied_series(output.b_upper, obs.sample_idx, len(truth.t))
        covered_applied = (b_true >= lower_applied - 1e-9) & (b_true <= upper_applied + 1e-9)
        metrics["interval_hold_coverage"] = float(covered_applied.mean())
    else:
        metrics.update(
            interval_sample_coverage=np.nan,
            interval_mean_width_usd=np.nan,
            interval_p95_width_usd=np.nan,
            interval_hold_coverage=np.nan,
        )
    return metrics


def phase_diagnostic_rows(
    truth: SimulationTruth,
    obs: Observations,
    output: AlgorithmOutput,
) -> pd.DataFrame:
    spread = output.diagnostics.get("relative_phase_spread")
    if spread is None:
        return pd.DataFrame()
    b_true, _ = true_limits(truth)
    sample_error = np.abs(output.b_hat - b_true[obs.sample_idx]).mean(axis=1)
    chosen = output.diagnostics.get("chosen_width_pp", np.full(len(obs.times), np.nan))
    count = output.diagnostics.get("phase_count", np.full(len(obs.times), np.nan))
    return pd.DataFrame(
        {
            "case_id": truth.spec.case_id,
            "algorithm": output.algorithm,
            "sample_index": np.arange(len(obs.times)),
            "time_hours": obs.times,
            "speed": truth.spec.speed,
            "scenario": truth.spec.scenario,
            "sample_hours": truth.spec.sample_hours,
            "phase_spread": spread,
            "mean_abs_limit_error_usd": sample_error,
            "chosen_width_pp": chosen,
            "phase_count": count,
        }
    )


def trajectory_frame(
    truth: SimulationTruth,
    obs: Observations,
    outputs: list[AlgorithmOutput],
) -> pd.DataFrame:
    b_true, _ = true_limits(truth)
    rows = []
    for fine_idx, t in enumerate(truth.t):
        sample_pos = int(np.searchsorted(obs.sample_idx, fine_idx, side="right") - 1)
        sample_pos = max(sample_pos, 0)
        for i in range(truth.spec.n_participants):
            base = {
                "case_id": truth.spec.case_id,
                "time_hours": float(t),
                "participant": i,
                "right_share": float(truth.rights[i]),
                "v_true": float(truth.v[fine_idx]),
                "c_true": float(truth.c[fine_idx, i]),
                "q_true": float(truth.q[fine_idx, i]),
                "b_true": float(b_true[fine_idx, i]),
                "display_z_held": int(obs.z[sample_pos]),
            }
            for output in outputs:
                row = dict(base)
                row["algorithm"] = output.algorithm
                row["b_applied"] = float(output.b_hat[sample_pos, i])
                if output.b_lower is not None:
                    row["b_lower_held"] = float(output.b_lower[sample_pos, i])
                    row["b_upper_held"] = float(output.b_upper[sample_pos, i])
                else:
                    row["b_lower_held"] = np.nan
                    row["b_upper_held"] = np.nan
                rows.append(row)
    return pd.DataFrame(rows)
