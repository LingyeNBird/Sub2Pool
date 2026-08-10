"""Full-factorial audit of width, phase count, offset, aggregation and coordinate.

This audit is deliberately separate from the frozen main test: it diagnoses
interactions and does not re-tune the primary configuration after test results.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from algorithms import estimate_total_center, multiphase_window
from run_study import ROOT, RAW, MAIN_FAMILIES, _simulate, _metric_row, seed_jobs

WIDTHS=[2.0,5.0,10.0,15.0,20.0,30.0]
PHASE_COUNTS=[1,2,3,5,7,10]
SCHEMES=["uniform","halfshift","golden","integer"]
AGGS=["mean","median","trimmed","huber","weighted","weighted_huber"]
COORDINATES=["display_interp","cost_progress","time_progress","proxy_progress","oracle_progress"]


def configs():
    for coordinate in COORDINATES:
        for width in WIDTHS:
            for m in PHASE_COUNTS:
                schemes=["uniform"] if m==1 else SCHEMES
                aggs=["mean"] if m==1 else AGGS
                for scheme in schemes:
                    for agg in aggs:
                        yield coordinate,width,m,scheme,agg


def one(family,seed):
    cycle=_simulate(family,seed)
    pcenter,_=estimate_total_center(cycle)
    rows=[]
    for coordinate,width,m,scheme,agg in configs():
        r=multiphase_window(cycle,width,m,pcenter,agg,scheme,
                            oracle_boundaries=(coordinate=="oracle_progress"),
                            name="phase_interaction",coordinate=coordinate)
        row=_metric_row(cycle,r,family,seed)
        row.update({"coordinate":coordinate,"width":width,"n_phases":m,
                    "phase_scheme":scheme,"aggregation":agg})
        rows.append(row)
    return rows


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--jobs',type=int,default=4);ap.add_argument('--per-family',type=int,default=3)
    args=ap.parse_args()
    folder=RAW/'phase_interaction';folder.mkdir(parents=True,exist_ok=True)
    manifest=seed_jobs(MAIN_FAMILIES,args.per_family,20261012)
    pd.DataFrame(manifest,columns=['scenario','seed']).to_csv(folder/'seed_manifest.csv',index=False)
    for family in MAIN_FAMILIES:
        jobspec=[x for x in manifest if x[0]==family]
        groups=Parallel(n_jobs=args.jobs,verbose=8)(delayed(one)(f,s) for f,s in jobspec)
        pd.DataFrame([r for g in groups for r in g]).to_csv(folder/f'{family}.csv.gz',index=False,compression='gzip')
    data=pd.concat([pd.read_csv(p) for p in sorted(folder.glob('*.csv.gz'))],ignore_index=True)
    summary=data.groupby(['coordinate','width','n_phases','phase_scheme','aggregation']).agg(
        cycles=('seed','count'),MAE_mean=('mae','mean'),
        max_over_P95=('max_over',lambda x:x.quantile(.95)),
        max_abs_P95=('max_abs',lambda x:x.quantile(.95)),
        wait_mean_minutes=('settlement_wait_mean_minutes','mean'),
    ).reset_index()
    summary.to_csv(folder/'interaction_summary.csv',index=False)

if __name__=='__main__':main()
