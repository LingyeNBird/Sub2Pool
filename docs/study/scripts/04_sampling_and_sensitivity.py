#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dynamic_limit.algorithms import multiscale_window  # noqa: E402
from dynamic_limit.experiment import load_yaml, make_final_specs  # noqa: E402
from dynamic_limit.generators import simulate_truth  # noqa: E402
from dynamic_limit.metrics import evaluate_output  # noqa: E402
from dynamic_limit.models import AlgorithmOutput  # noqa: E402
from dynamic_limit.observation import make_observations, true_limits  # noqa: E402
from dynamic_limit.particle_filter import ParticleFilterConfig, particle_filter  # noqa: E402


def from_state(name, obs, rights, q_hat, v_hat):
    rem = np.maximum(100.0 * rights[None, :] - q_hat, 0.0)
    b = rem * v_hat[:, None] / 100.0
    return AlgorithmOutput(
        algorithm=name,
        times=obs.times,
        b_hat=b,
        l_hat=obs.c_obs + b,
        q_hat=q_hat,
        v_hat=v_hat,
    )
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="量化误差与采样敏感性消融实验。默认复现 180 个冻结案例。",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=180,
        help="从最终矩阵中确定性抽取的最大案例数（默认：180）",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="仅运行 4 个案例，验证归档流水线",
    )
    return parser.parse_args()




def main():
    args = parse_args()
    if args.max_cases < 1:
        raise SystemExit("--max-cases 必须大于 0")
    cfg = load_yaml(ROOT / "config" / "final_test.yaml")
    specs, _ = make_final_specs(cfg)
    # Deterministic spread across the final matrix; no overlap with tuning seeds.
    case_count = 4 if args.smoke else args.max_cases
    chosen = specs[:: max(1, len(specs) // case_count)][:case_count]
    with open(ROOT / "results" / "selected_parameters.json", encoding="utf-8") as f:
        selected = json.load(f)
    rows = []
    for idx, spec in enumerate(chosen, 1):
        truth = simulate_truth(spec)
        obs_round = make_observations(truth, round_amounts=True)
        obs_exact = make_observations(truth, round_amounts=False)
        obs_oracle_p = replace(obs_exact, z=truth.p[obs_exact.sample_idx].copy())
        seed = (spec.seed + 7919) % (2**32 - 1)
        base_kwargs = dict(
            particles=int(cfg["particle_count"]),
            observation_soft_sigma_pp=float(selected["pf_observation_soft_sigma_pp"]),
            timing_dirichlet_alpha=float(selected["pf_timing_dirichlet_alpha"]),
        )
        pf_round = particle_filter(obs_round, truth.rights, seed, ParticleFilterConfig(**base_kwargs))
        pf_round.algorithm = "pf_rounded_unknown_quantizer"
        pf_exact = particle_filter(obs_exact, truth.rights, seed, ParticleFilterConfig(**base_kwargs))
        pf_exact.algorithm = "pf_exact_amount_unknown_quantizer"
        pf_known = particle_filter(
            obs_round,
            truth.rights,
            seed,
            ParticleFilterConfig(**base_kwargs, known_quantizer=spec.quantizer),
        )
        pf_known.algorithm = "pf_rounded_known_quantizer"

        mw_round = multiscale_window(obs_round, truth.rights, widths_pp=selected["multiscale_widths_pp"])
        mw_round.algorithm = "multiscale_rounded_integer_progress"
        mw_exact_amt = multiscale_window(obs_exact, truth.rights, widths_pp=selected["multiscale_widths_pp"])
        mw_exact_amt.algorithm = "multiscale_exact_amount_integer_progress"
        mw_oracle_p = multiscale_window(obs_oracle_p, truth.rights, widths_pp=selected["multiscale_widths_pp"])
        mw_oracle_p.algorithm = "multiscale_exact_amount_exact_progress"

        q_true_s = truth.q[obs_round.sample_idx]
        v_true_s = truth.v[obs_round.sample_idx]
        pf_oracle_v = from_state("pf_q_estimate_oracle_current_v", obs_round, truth.rights, pf_round.q_hat, v_true_s)
        pf_oracle_q = from_state("pf_oracle_q_v_estimate", obs_round, truth.rights, q_true_s, pf_round.v_hat)
        oracle_state = from_state("oracle_sample_state_held", obs_round, truth.rights, q_true_s, v_true_s)

        for out in [
            pf_round,
            pf_exact,
            pf_known,
            mw_round,
            mw_exact_amt,
            mw_oracle_p,
            pf_oracle_v,
            pf_oracle_q,
            oracle_state,
        ]:
            rows.append(evaluate_output(truth, obs_round if "multiscale_exact" not in out.algorithm else obs_exact, out))
        if idx % 30 == 0:
            print(f"{idx}/{len(chosen)}", flush=True)

    raw = pd.DataFrame(rows)
    raw.to_csv(ROOT / "results" / "sensitivity_raw.csv.gz", index=False, compression="gzip")
    summary = (
        raw.groupby("algorithm", as_index=False)
        .agg(
            cases=("case_id", "nunique"),
            mean_mae_usd=("mae_usd", "mean"),
            median_mae_usd=("mae_usd", "median"),
            p95_case_mae_usd=("mae_usd", lambda s: s.quantile(0.95)),
            mean_q_mae_pp=("q_sample_mae_pp", "mean"),
            mean_v_mae_usd=("v_sample_mae_usd", "mean"),
            mean_hold_increment_usd=("hold_mae_increment_usd", "mean"),
        )
        .sort_values("mean_mae_usd")
    )
    summary.to_csv(ROOT / "results" / "sensitivity_summary.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
