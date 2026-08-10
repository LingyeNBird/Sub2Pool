"""Reproducible experiment runner for the V2 final study.

Suites are independent and resumable. Every stochastic suite writes a seed
manifest before running and stores one gzip CSV per family or study component.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from dataclasses import asdict

import numpy as np
import pandas as pd
import yaml
from joblib import Parallel, delayed

SUPPORT = Path(__file__).resolve().parent / "_support" / "attribution"
sys.path.insert(0, str(SUPPORT))

from algorithms import (
    AttributionResult,
    adaptive_multiphase,
    adjacent_proportional,
    estimate_total_center,
    fixed_window,
    global_proportional,
    moving_local_window,
    multiphase_window,
    set_attribution,
    set_box_midpoint_from_result,
    tv_attribution,
)
from feasible import minimax_face_audit
from metrics import evaluate
from models import CycleData, truncate_cycle
from scenarios import MAIN_FAMILIES, OOD_FAMILIES, main_spec, ood_spec, seed_jobs
from simulate import ScenarioSpec, X_MIN, X_MAX, simulate_cycle

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config" / "attribution_study.yaml").read_text())
RAW = ROOT / "results/raw"
SUMMARY = ROOT / "results/summary"
RAW.mkdir(parents=True, exist_ok=True)
SUMMARY.mkdir(parents=True, exist_ok=True)

PHASE_COUNTS = [1, 2, 3, 5, 7, 10]
PHASE_SCHEMES = ["uniform", "halfshift", "golden", "integer"]
PHASE_AGGS = ["mean", "median", "trimmed", "huber", "weighted", "weighted_huber"]
WIDTH_GRID = [1, 2, 3, 4, 5, 7, 10, 15, 20, 30]
THRESHOLDS = [0.05, 0.10, 0.15, 0.25, 0.40, 0.60]
MAX_WIDTHS = [10, 20, 30]
ADAPTIVE_WIDTHS = [1, 2, 3, 4, 5, 7, 10, 15, 20]
DEV_FAMILIES = MAIN_FAMILIES[:6]


def _common(cycle, family, seed):
    return {
        "scenario": family,
        "seed": int(seed),
        "n_users": cycle.n_users,
        "n_events": len(cycle.event_times),
        "target_progress": float(cycle.metadata.get("target_progress", cycle.true_total)),
        "true_total": cycle.true_total,
        "observed_final": cycle.observed_final,
        "sampling_minutes": float(cycle.metadata.get("sampling_minutes", np.nan)),
        "rate_process": cycle.metadata.get("rate_process", ""),
        "schedule": cycle.metadata.get("schedule", ""),
        "theta_true": float(cycle.quantizer_params.get("theta", np.nan)),
        "quantizer": cycle.quantizer_name,
    }


def _metric_row(cycle, result, family, seed):
    row, _ = evaluate(cycle, result)
    row.update(_common(cycle, family, seed))
    return row


def _simulate(family, seed, ood=False):
    return simulate_cycle(ood_spec(family, int(seed)) if ood else main_spec(family, int(seed)))


def _selected_path():
    return RAW / "phase" / "selected_phase_configs.json"


def _load_selected():
    path = _selected_path()
    if path.exists():
        return json.loads(path.read_text())
    # Frozen fallback from the prior independent V2 development run. It is used
    # only if the local phase suite has not yet been executed.
    cfg = CFG["selected_phase_configuration"]
    acfg = CFG["selected_adaptive_configuration"]
    return {
        "static_accuracy": dict(cfg),
        "static_tail": dict(cfg),
        "adaptive_accuracy": dict(acfg),
        "adaptive_tail": dict(acfg),
        "moving_backward_accuracy": {"width": 20.0, "orientation": "backward"},
        "moving_centered_accuracy": {"width": 15.0, "orientation": "centered"},
        "moving_forward_accuracy": {"width": 20.0, "orientation": "forward"},
        "selection_rule": "frozen fallback; replaced by local development when available",
    }


def _fast_algorithms(cycle, selected, reconciliation="euclidean"):
    pcenter, _ = estimate_total_center(cycle)
    static = selected["static_accuracy"]
    adaptive = selected["adaptive_accuracy"]
    return [
        global_proportional(cycle, pcenter, reconciliation),
        adjacent_proportional(cycle, pcenter, reconciliation),
        fixed_window(cycle, 5.0, pcenter, reconciliation, "window_5pp"),
        multiphase_window(
            cycle, static["width"], static["n_phases"], pcenter,
            static["aggregation"], static["phase_scheme"], False,
            reconciliation, "phase_selected",
        ),
        adaptive_multiphase(
            cycle, ADAPTIVE_WIDTHS, adaptive["threshold"], adaptive["n_phases"],
            pcenter, adaptive["aggregation"], adaptive["phase_scheme"],
            reconciliation, "adaptive_selected",
        ),
        moving_local_window(cycle, 20.0, "backward", pcenter, reconciliation, "moving_backward"),
        moving_local_window(cycle, 15.0, "centered", pcenter, reconciliation, "moving_centered"),
        moving_local_window(cycle, 20.0, "forward", pcenter, reconciliation, "moving_forward"),
    ]


def _lp_algorithms(cycle, selected):
    pcenter, _ = estimate_total_center(cycle)
    static = selected["static_accuracy"]
    adaptive = selected["adaptive_accuracy"]
    base = [
        global_proportional(cycle, pcenter),
        fixed_window(cycle, 5.0, pcenter, name="window_5pp"),
        multiphase_window(
            cycle, static["width"], static["n_phases"], pcenter,
            static["aggregation"], static["phase_scheme"], name="phase_selected",
        ),
        adaptive_multiphase(
            cycle, ADAPTIVE_WIDTHS, adaptive["threshold"], adaptive["n_phases"],
            pcenter, adaptive["aggregation"], adaptive["phase_scheme"],
            name="adaptive_selected",
        ),
        moving_local_window(cycle, 15.0, "centered", pcenter, name="moving_centered"),
        tv_attribution(cycle, name="tv_minimum_variation"),
    ]
    set_mid = set_attribution(cycle, "midpoint_lex", name="set_midpoint_lex")
    base.append(set_mid)
    if set_mid.success:
        base.append(set_box_midpoint_from_result(set_mid, "set_box_midpoint"))
    return base


def _chunk_metrics(family, seeds, kind, selected, ood=False, reconciliation="euclidean"):
    rows = []
    for seed in seeds:
        cycle = _simulate(family, int(seed), ood)
        algorithms = _fast_algorithms(cycle, selected, reconciliation) if kind == "fast" else _lp_algorithms(cycle, selected)
        for result in algorithms:
            rows.append(_metric_row(cycle, result, family, int(seed)))
    return rows


def _run_family_metrics(family, seeds, kind, selected, output, jobs, ood=False, reconciliation="euclidean"):
    output.parent.mkdir(parents=True, exist_ok=True)
    chunks = [seeds[i:i+40] for i in range(0, len(seeds), 40)]
    groups = Parallel(n_jobs=jobs, verbose=8)(
        delayed(_chunk_metrics)(family, chunk, kind, selected, ood, reconciliation)
        for chunk in chunks
    )
    pd.DataFrame([r for g in groups for r in g]).to_csv(output, index=False, compression="gzip")


def run_main(kind, jobs, ood=False):
    selected = _load_selected()
    if ood:
        families = OOD_FAMILIES
        reps = int(CFG["sample_sizes"][f"ood_{kind}_per_family"])
        master = int(CFG["seeds"][f"ood_{kind}"])
        folder = RAW / f"ood_{kind}"
    else:
        families = MAIN_FAMILIES
        reps = int(CFG["sample_sizes"][f"main_{kind}_per_family"])
        master = int(CFG["seeds"][f"main_{kind}"])
        folder = RAW / f"main_{kind}"
    folder.mkdir(parents=True, exist_ok=True)
    manifest = seed_jobs(families, reps, master)
    pd.DataFrame(manifest, columns=["scenario", "seed"]).to_csv(folder / "seed_manifest.csv", index=False)
    for family in families:
        out = folder / f"{family}.csv.gz"
        seeds = [s for f, s in manifest if f == family]
        _run_family_metrics(family, seeds, kind, selected, out, jobs, ood)


# ---------------------------------------------------------------------------
# Phase development and validation
# ---------------------------------------------------------------------------

def _phase_configs():
    result = []
    for m in PHASE_COUNTS:
        schemes = ["uniform"] if m == 1 else PHASE_SCHEMES
        aggregations = ["mean"] if m == 1 else PHASE_AGGS
        for scheme in schemes:
            for aggregation in aggregations:
                result.append((m, scheme, aggregation))
    return result


def _phase_screen_one(family, seed):
    cycle = _simulate(family, seed)
    pcenter, _ = estimate_total_center(cycle)
    rows = []
    for m, scheme, aggregation in _phase_configs():
        result = multiphase_window(cycle, 5.0, m, pcenter, aggregation, scheme, name="phase_screen")
        row = _metric_row(cycle, result, family, seed)
        row.update({"width": 5.0, "n_phases": m, "phase_scheme": scheme, "aggregation": aggregation})
        rows.append(row)
    return rows


def _pareto_mask(frame, columns):
    values = frame[columns].to_numpy(float)
    keep = np.ones(len(frame), bool)
    for i in range(len(frame)):
        dominates = np.all(values <= values[i] + 1e-12, axis=1) & np.any(values < values[i] - 1e-12, axis=1)
        if np.any(dominates):
            keep[i] = False
    return keep


def _phase_validate_one(family, seed, designs):
    cycle = _simulate(family, seed)
    pcenter, _ = estimate_total_center(cycle)
    rows = []
    for m, scheme, aggregation in designs:
        for width in WIDTH_GRID:
            result = multiphase_window(cycle, width, m, pcenter, aggregation, scheme, name="phase_width")
            row = _metric_row(cycle, result, family, seed)
            row.update({"kind":"static", "width":width, "n_phases":m, "phase_scheme":scheme, "aggregation":aggregation, "threshold":np.nan, "max_width":np.nan, "orientation":""})
            rows.append(row)
    best_design = designs[0]
    m, scheme, aggregation = best_design
    for threshold in THRESHOLDS:
        for max_width in MAX_WIDTHS:
            candidates = [w for w in ADAPTIVE_WIDTHS if w <= max_width]
            result = adaptive_multiphase(cycle, candidates, threshold, m, pcenter, aggregation, scheme, name="adaptive_validation")
            row = _metric_row(cycle, result, family, seed)
            row.update({"kind":"adaptive", "width":np.nan, "n_phases":m, "phase_scheme":scheme, "aggregation":aggregation, "threshold":threshold, "max_width":max_width, "orientation":""})
            rows.append(row)
    for orientation in ["backward", "centered", "forward"]:
        for width in WIDTH_GRID:
            result = moving_local_window(cycle, width, orientation, pcenter, name="moving_validation")
            row = _metric_row(cycle, result, family, seed)
            row.update({"kind":"moving", "width":width, "n_phases":np.nan, "phase_scheme":"", "aggregation":"", "threshold":np.nan, "max_width":np.nan, "orientation":orientation})
            rows.append(row)
    return rows


def _quantile_batch_se(values, p=0.95, batches=20):
    x = np.asarray(values, float)
    if len(x) < batches * 5:
        return np.nan
    parts = np.array_split(x, batches)
    q = np.array([np.quantile(part, p) for part in parts if len(part)])
    return float(q.std(ddof=1) / np.sqrt(len(q)))


def _choose(summary, mean_col="MAE_mean", tail_col="max_over_P95", wait_col="wait_mean"):
    best = summary.loc[summary[mean_col].idxmin()]
    margin = float(best.get("MAE_se", 0.0))
    eligible = summary[summary[mean_col] <= best[mean_col] + margin + 1e-12]
    accuracy = eligible.sort_values([wait_col, tail_col, mean_col]).iloc[0]
    tail_best = summary.loc[summary[tail_col].idxmin()]
    tail_margin = float(tail_best.get("max_over_P95_se", 0.0))
    if not np.isfinite(tail_margin):
        tail_margin = 0.0
    tail_eligible = summary[summary[tail_col] <= tail_best[tail_col] + tail_margin + 1e-12]
    tail = tail_eligible.sort_values([wait_col, mean_col]).iloc[0]
    return accuracy, tail


def run_phase(jobs):
    folder = RAW / "phase"
    folder.mkdir(parents=True, exist_ok=True)
    screen_jobs = seed_jobs(DEV_FAMILIES, int(CFG["sample_sizes"]["phase_screen_per_family"]), int(CFG["seeds"]["phase_development"]))
    pd.DataFrame(screen_jobs, columns=["scenario","seed"]).to_csv(folder / "screen_manifest.csv", index=False)
    groups = Parallel(n_jobs=jobs, verbose=8)(delayed(_phase_screen_one)(f,s) for f,s in screen_jobs)
    screen = pd.DataFrame([r for g in groups for r in g])
    screen.to_csv(folder / "phase_screen.csv.gz", index=False, compression="gzip")
    screen_summary = screen.groupby(["n_phases","phase_scheme","aggregation"]).agg(
        MAE_mean=("mae","mean"),
        max_over_P95=("max_over",lambda x:x.quantile(.95)),
        wait_mean=("settlement_wait_mean_minutes","mean"),
        runs=("seed","count"),
    ).reset_index()
    screen_summary["pareto"] = _pareto_mask(screen_summary, ["MAE_mean","max_over_P95","wait_mean"])
    cand = screen_summary[screen_summary.pareto].copy()
    for col in ["MAE_mean","max_over_P95","wait_mean"]:
        cand[col+"_rank"] = cand[col].rank(method="average")
    cand["rank_sum"] = cand[["MAE_mean_rank","max_over_P95_rank","wait_mean_rank"]].sum(axis=1)
    selected_designs = [(int(r.n_phases),str(r.phase_scheme),str(r.aggregation)) for r in cand.sort_values("rank_sum").head(8).itertuples()]
    screen_summary.to_csv(folder / "phase_screen_summary.csv", index=False)
    pd.DataFrame(selected_designs, columns=["n_phases","phase_scheme","aggregation"]).to_csv(folder / "designs_for_validation.csv", index=False)

    validation_jobs = seed_jobs(DEV_FAMILIES, int(CFG["sample_sizes"]["phase_validation_per_family"]), int(CFG["seeds"]["phase_validation"]))
    pd.DataFrame(validation_jobs, columns=["scenario","seed"]).to_csv(folder / "validation_manifest.csv", index=False)
    groups = Parallel(n_jobs=jobs, verbose=8)(delayed(_phase_validate_one)(f,s,selected_designs) for f,s in validation_jobs)
    validation = pd.DataFrame([r for g in groups for r in g])
    validation.to_csv(folder / "phase_validation.csv.gz", index=False, compression="gzip")

    static = validation[validation.kind=="static"]
    static_summary = static.groupby(["width","n_phases","phase_scheme","aggregation"]).agg(
        MAE_mean=("mae","mean"), MAE_se=("mae",lambda x:x.std(ddof=1)/np.sqrt(len(x))),
        max_over_P95=("max_over",lambda x:x.quantile(.95)),
        max_over_P95_se=("max_over",_quantile_batch_se),
        wait_mean=("settlement_wait_mean_minutes","mean"), wait_P95=("settlement_wait_mean_minutes",lambda x:x.quantile(.95)), runs=("seed","count")
    ).reset_index()
    sacc, stail = _choose(static_summary)
    static_summary.to_csv(folder / "static_validation_summary.csv", index=False)

    adaptive = validation[validation.kind=="adaptive"]
    adaptive_summary = adaptive.groupby(["threshold","max_width","n_phases","phase_scheme","aggregation"]).agg(
        MAE_mean=("mae","mean"), MAE_se=("mae",lambda x:x.std(ddof=1)/np.sqrt(len(x))),
        max_over_P95=("max_over",lambda x:x.quantile(.95)), max_over_P95_se=("max_over",_quantile_batch_se),
        wait_mean=("settlement_wait_mean_minutes","mean"), wait_P95=("settlement_wait_mean_minutes",lambda x:x.quantile(.95)), runs=("seed","count")
    ).reset_index()
    aacc, atail = _choose(adaptive_summary)
    adaptive_summary.to_csv(folder / "adaptive_validation_summary.csv", index=False)

    moving = validation[validation.kind=="moving"]
    moving_summary = moving.groupby(["width","orientation"]).agg(
        MAE_mean=("mae","mean"), MAE_se=("mae",lambda x:x.std(ddof=1)/np.sqrt(len(x))),
        max_over_P95=("max_over",lambda x:x.quantile(.95)), max_over_P95_se=("max_over",_quantile_batch_se),
        wait_mean=("settlement_wait_mean_minutes","mean"), wait_P95=("settlement_wait_mean_minutes",lambda x:x.quantile(.95)), runs=("seed","count")
    ).reset_index()
    moving_summary.to_csv(folder / "moving_validation_summary.csv", index=False)
    selected = {
        "static_accuracy": {"width":float(sacc.width),"n_phases":int(sacc.n_phases),"phase_scheme":str(sacc.phase_scheme),"aggregation":str(sacc.aggregation)},
        "static_tail": {"width":float(stail.width),"n_phases":int(stail.n_phases),"phase_scheme":str(stail.phase_scheme),"aggregation":str(stail.aggregation)},
        "adaptive_accuracy": {"threshold":float(aacc.threshold),"max_width":int(aacc.max_width),"n_phases":int(aacc.n_phases),"phase_scheme":str(aacc.phase_scheme),"aggregation":str(aacc.aggregation)},
        "adaptive_tail": {"threshold":float(atail.threshold),"max_width":int(atail.max_width),"n_phases":int(atail.n_phases),"phase_scheme":str(atail.phase_scheme),"aggregation":str(atail.aggregation)},
        "selection_rule":"independent development/validation; one-SE mean point and separate P95-over point",
    }
    for orientation in ["backward","centered","forward"]:
        sub = moving_summary[moving_summary.orientation==orientation]
        acc, tail = _choose(sub)
        selected[f"moving_{orientation}_accuracy"]={"width":float(acc.width),"orientation":orientation}
        selected[f"moving_{orientation}_tail"]={"width":float(tail.width),"orientation":orientation}
    _selected_path().write_text(json.dumps(selected, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Minimax face, timestamp ties, reconciliation and saturation boundary
# ---------------------------------------------------------------------------

def _center_one(family, seed):
    cycle = _simulate(family, seed)
    audit = minimax_face_audit(cycle)
    if not audit.success:
        return [{"scenario":family,"seed":seed,"success":False,"message":audit.message}]
    meta = audit.metadata or {}
    results = [
        set_attribution(cycle,"primary_vertex",name="set_primary_vertex_diagnostic"),
        set_attribution(cycle,"midpoint_lex",name="set_midpoint_lex"),
        tv_attribution(cycle,name="tv_minimum_variation"),
    ]
    tv_anchor = set_attribution(cycle,"anchor_lex",anchor=results[-1].estimate,name="set_tv_anchor_lex")
    results.append(tv_anchor)
    rows=[]
    for result in results:
        row=_metric_row(cycle,result,family,seed)
        row.update({
            "face_diameter_linf":float(meta.get("face_diameter_linf",np.nan)),
            "face_lower_json":json.dumps(np.asarray(meta.get("face_lower",[])).tolist()),
            "face_upper_json":json.dumps(np.asarray(meta.get("face_upper",[])).tolist()),
        })
        rows.append(row)
    return rows


def run_center(jobs):
    folder=RAW/"audits";folder.mkdir(parents=True,exist_ok=True)
    manifest=seed_jobs(MAIN_FAMILIES,12,int(CFG["seeds"]["main_lp"])+901)
    pd.DataFrame(manifest,columns=["scenario","seed"]).to_csv(folder/"center_manifest.csv",index=False)
    groups=Parallel(n_jobs=jobs,verbose=8)(delayed(_center_one)(f,s) for f,s in manifest)
    pd.DataFrame([r for g in groups for r in g]).to_csv(folder/"center_nonuniqueness.csv.gz",index=False,compression="gzip")


def _tie_one(seed):
    import legacy_lp_v1
    rng=np.random.default_rng(seed)
    spec=ScenarioSpec("paired_timestamps",int(rng.choice([2,2,5])),int(rng.integers(80,321)),float(rng.uniform(20,80)),float(rng.choice([5,10,30,60])),"paired_ties",str(rng.choice(["smooth","piecewise","jump"])),float(rng.uniform(.6,1.3)),0.0,"fixed_offset",None,.15,float(rng.uniform(0,.04)),int(seed))
    cycle=simulate_cycle(spec)
    exact=set_attribution(cycle,"midpoint_lex")
    legacy=legacy_lp_v1.identification_region(cycle,exact_feasible_center=False)
    truth=cycle.true_user_totals
    return {
        "seed":int(seed),"n_users":cycle.n_users,"n_events":len(cycle.event_times),
        "simultaneous_group_count":int((exact.metadata or {}).get("simultaneous_group_count",0)),
        "exact_width_mean":float(np.mean(exact.upper-exact.lower)),
        "legacy_width_mean":float(np.mean(legacy.upper-legacy.lower)),
        "exact_width_max":float(np.max(exact.upper-exact.lower)),
        "legacy_width_max":float(np.max(legacy.upper-legacy.lower)),
        "legacy_minus_exact_width_mean":float(np.mean((legacy.upper-legacy.lower)-(exact.upper-exact.lower))),
        "exact_coverage":bool(np.all((truth>=exact.lower-1e-7)&(truth<=exact.upper+1e-7))),
        "legacy_coverage":bool(np.all((truth>=legacy.lower-1e-7)&(truth<=legacy.upper+1e-7))),
    }


def run_ties(jobs):
    folder=RAW/"audits";folder.mkdir(parents=True,exist_ok=True)
    ss=np.random.SeedSequence(int(CFG["seeds"]["tie"]));seeds=[int(x.generate_state(1)[0]) for x in ss.spawn(int(CFG["sample_sizes"]["tie_cycles"]))]
    pd.DataFrame({"seed":seeds}).to_csv(folder/"tie_manifest.csv",index=False)
    rows=Parallel(n_jobs=jobs,verbose=8)(delayed(_tie_one)(s) for s in seeds)
    pd.DataFrame(rows).to_csv(folder/"tie_coupling.csv.gz",index=False,compression="gzip")


def run_reconciliation(jobs):
    folder=RAW/"reconciliation";folder.mkdir(parents=True,exist_ok=True)
    families=["smooth_staggered","jump_staggered","correlated_extreme","adversarial_alignment"]
    manifest=seed_jobs(families,150,int(CFG["seeds"]["main_fast"])+777)
    pd.DataFrame(manifest,columns=["scenario","seed"]).to_csv(folder/"seed_manifest.csv",index=False)
    selected=_load_selected()
    def one(f,s):
        c=_simulate(f,s);rows=[]
        for rule in ["euclidean","proportional"]:
            for r in _fast_algorithms(c,selected,rule):
                if r.name in ["phase_selected","adaptive_selected","moving_centered"]:
                    row=_metric_row(c,r,f,s);row["reconciliation"]=rule;rows.append(row)
        return rows
    groups=Parallel(n_jobs=jobs,verbose=8)(delayed(one)(f,s) for f,s in manifest)
    pd.DataFrame([r for g in groups for r in g]).to_csv(folder/"reconciliation_metrics.csv.gz",index=False,compression="gzip")


def run_boundary(jobs):
    folder=RAW/"boundary";folder.mkdir(parents=True,exist_ok=True)
    ss=np.random.SeedSequence(int(CFG["seeds"]["quantizer"])+111)
    seeds=[int(x.generate_state(1)[0]) for x in ss.spawn(500)]
    def one(seed):
        rng=np.random.default_rng(seed)
        spec=ScenarioSpec("saturation_boundary",int(rng.choice([2,2,5])),int(rng.integers(150,501)),float(rng.uniform(99,112)),float(rng.choice([5,10,30])),str(rng.choice(["mixed","staggered","bursty"])),str(rng.choice(["smooth","piecewise","jump"])),float(rng.uniform(.7,1.4)),0.0,"fixed_offset",None,.15,float(rng.uniform(0,.05)),seed)
        c=simulate_cycle(spec);rows=[]
        for r in [tv_attribution(c,name="tv_right_censored"),set_attribution(c,"midpoint_lex",name="set_right_censored")]:
            row=_metric_row(c,r,"saturation_boundary",seed);row["hidden_above_100"]=float(max(0,c.true_total-100));rows.append(row)
        return rows
    groups=Parallel(n_jobs=jobs,verbose=8)(delayed(one)(s) for s in seeds)
    pd.DataFrame([r for g in groups for r in g]).to_csv(folder/"boundary_metrics.csv.gz",index=False,compression="gzip")
    pd.DataFrame({"seed":seeds}).to_csv(folder/"seed_manifest.csv",index=False)


# ---------------------------------------------------------------------------
# Quantizer stress
# ---------------------------------------------------------------------------

def run_quantizer(jobs):
    folder=RAW/"quantizer";folder.mkdir(parents=True,exist_ok=True)
    quantizers=["fixed_offset","floor","nearest","irregular","switching_offset"]
    samplings=[5,30,120]
    reps=int(CFG["sample_sizes"]["quantizer_cycles_per_cell"])
    ss=np.random.SeedSequence(int(CFG["seeds"]["quantizer"]));children=ss.spawn(len(quantizers)*len(samplings)*reps)
    jobspec=[];k=0
    for quantizer in quantizers:
        for sampling in samplings:
            for _ in range(reps):
                jobspec.append((quantizer,sampling,int(children[k].generate_state(1)[0])));k+=1
    pd.DataFrame(jobspec,columns=["quantizer","sampling_minutes","seed"]).to_csv(folder/"seed_manifest.csv",index=False)
    selected=_load_selected()
    def one(quantizer,sampling,seed):
        rng=np.random.default_rng(seed)
        family=str(rng.choice(MAIN_FAMILIES[:6]));base=main_spec(family,seed)
        spec=ScenarioSpec(base.name,base.n_users,base.n_events,base.target_progress,float(sampling),base.schedule,base.rate_process,base.cost_sigma,base.dominance,quantizer,None,.18,base.heavy_event_prob,seed)
        c=simulate_cycle(spec);p,_=estimate_total_center(c);static=selected["static_accuracy"]
        methods=[global_proportional(c,p),multiphase_window(c,static["width"],static["n_phases"],p,static["aggregation"],static["phase_scheme"],name="phase_selected"),tv_attribution(c,name="tv_fixed_offset_model"),set_attribution(c,"midpoint_lex",name="set_fixed_offset_model")]
        rows=[]
        for r in methods:
            row=_metric_row(c,r,f"{quantizer}_{sampling}",seed);row["generator_quantizer"]=quantizer;rows.append(row)
        return rows
    groups=Parallel(n_jobs=jobs,verbose=8)(delayed(one)(*j) for j in jobspec)
    pd.DataFrame([r for g in groups for r in g]).to_csv(folder/"quantizer_metrics.csv.gz",index=False,compression="gzip")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--suite",choices=["phase","main_fast","main_lp","ood_fast","ood_lp","center","ties","reconciliation","boundary","quantizer","all"],default="all")
    parser.add_argument("--jobs",type=int,default=max(1,min(4,os.cpu_count() or 1)))
    args=parser.parse_args()
    suites=[args.suite] if args.suite!="all" else ["phase","center","ties","main_fast","main_lp","ood_fast","ood_lp","reconciliation","boundary","quantizer"]
    for suite in suites:
        if suite=="phase":run_phase(args.jobs)
        elif suite=="main_fast":run_main("fast",args.jobs,False)
        elif suite=="main_lp":run_main("lp",args.jobs,False)
        elif suite=="ood_fast":run_main("fast",args.jobs,True)
        elif suite=="ood_lp":run_main("lp",args.jobs,True)
        elif suite=="center":run_center(args.jobs)
        elif suite=="ties":run_ties(args.jobs)
        elif suite=="reconciliation":run_reconciliation(args.jobs)
        elif suite=="boundary":run_boundary(args.jobs)
        elif suite=="quantizer":run_quantizer(args.jobs)

if __name__=="__main__":
    main()
