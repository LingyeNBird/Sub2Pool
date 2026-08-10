"""Cross-cycle prior study with stable, drifting, and abrupt regimes."""
from __future__ import annotations
import argparse, os
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
from joblib import Parallel, delayed

from algorithms import tv_attribution
from metrics import evaluate
from models import CycleData
from quantizers import fixed_offset_quantize
from scenarios import main_spec, seed_jobs
from simulate import X_MIN, X_MAX, simulate_cycle

ROOT=Path(__file__).resolve().parents[3]
CFG=yaml.safe_load((ROOT/'config'/'attribution_study.yaml').read_text())
OUT=ROOT/'results/raw/history';OUT.mkdir(parents=True,exist_ok=True)


def _replace_rate(base, x, theta):
    q=base.event_costs*np.asarray(x,float)
    cumulative=np.cumsum(q)
    idx=np.searchsorted(base.event_times,base.sample_times,side='right')-1
    progress=np.where(idx>=0,cumulative[np.maximum(idx,0)],0.0)
    z=fixed_offset_quantize(progress,theta)
    return CycleData(base.duration_minutes,base.event_times,base.event_users,base.event_costs,np.asarray(x,float),q,base.sample_times,progress,z,base.n_users,'fixed_offset',{'theta':float(theta)},base.scenario_name,base.seed,{**base.metadata,'history_theta':float(theta)})


def _sequence(regime, seed, cycles=8):
    rng=np.random.default_rng(seed)
    x0=float(rng.uniform(X_MIN+.004,X_MAX-.004))
    if regime=='stable':
        levels=np.full(cycles,x0)+rng.normal(0,0.0005,cycles)
    elif regime=='drift':
        end=float(np.clip(x0+rng.choice([-1,1])*rng.uniform(.006,.016),X_MIN+.001,X_MAX-.001))
        levels=np.linspace(x0,end,cycles)+rng.normal(0,0.0004,cycles)
    elif regime=='abrupt':
        x1=float(np.clip(x0+rng.choice([-1,1])*rng.uniform(.012,.022),X_MIN+.001,X_MAX-.001))
        levels=np.r_[np.full(cycles//2,x0),np.full(cycles-cycles//2,x1)]+rng.normal(0,0.0004,cycles)
    else: raise ValueError(regime)
    levels=np.clip(levels,X_MIN,X_MAX)
    prior=None;rows=[]
    for k,level in enumerate(levels):
        spec=main_spec(str(rng.choice(['constant_mixed','smooth_mixed','piecewise_mixed'])),int(rng.integers(1,2**31-1)))
        base=simulate_cycle(spec)
        local=np.clip(level+rng.normal(0,0.0006,len(base.event_times)),X_MIN,X_MAX)
        theta=float(rng.uniform())
        cycle=_replace_rate(base,local,theta)
        nohist=tv_attribution(cycle,name='tv_no_history')
        # Compatibility-aware forgetting uses only current observations: compare
        # the cheap observed mean slope with the previous-cycle prior.
        observed_mean=float(cycle.observed_final/max(cycle.event_costs.sum(),1e-12))
        if prior is None:
            weight=0.0
        else:
            mismatch=abs(observed_mean-prior)
            weight=float(0.40*np.exp(-(mismatch/0.0045)**2))
        hist=tv_attribution(cycle,prior=prior,prior_weight=weight,name='tv_history_forgetting')
        fixed=tv_attribution(cycle,prior=prior,prior_weight=(0.40 if prior is not None else 0.0),name='tv_history_fixed')
        for result in [nohist,hist,fixed]:
            row,_=evaluate(cycle,result)
            row.update({'regime':regime,'sequence_seed':seed,'cycle_index':k,'true_mean_x':float(np.average(local,weights=cycle.event_costs)),'prior_x':prior if prior is not None else np.nan,'prior_weight':weight if result.name=='tv_history_forgetting' else (0.40 if result.name=='tv_history_fixed' and prior is not None else 0.0)})
            rows.append(row)
        if hist.success:
            prior=float((hist.metadata or {}).get('mean_inverse_rate',observed_mean))
        elif nohist.success:
            prior=float((nohist.metadata or {}).get('mean_inverse_rate',observed_mean))
        else:
            prior=observed_mean
    return rows


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--jobs',type=int,default=max(1,min(4,os.cpu_count() or 1)));a=ap.parse_args()
    regimes=['stable','drift','abrupt'];reps=int(CFG['sample_sizes']['history_sequences_per_regime'])
    manifest=seed_jobs(regimes,reps,int(CFG['seeds']['history']))
    pd.DataFrame(manifest,columns=['regime','seed']).to_csv(OUT/'seed_manifest.csv',index=False)
    groups=Parallel(n_jobs=a.jobs,verbose=8)(delayed(_sequence)(r,s) for r,s in manifest)
    df=pd.DataFrame([x for g in groups for x in g]);df.to_csv(OUT/'history_metrics.csv.gz',index=False,compression='gzip')
    summary=df.groupby(['regime','algorithm']).agg(MAE_mean=('mae','mean'),max_over_P95=('max_over',lambda x:x.quantile(.95)),failure_rate=('success',lambda x:1-np.mean(x)),runs=('cycle_index','count')).reset_index()
    summary.to_csv(OUT/'history_summary.csv',index=False)
if __name__=='__main__':main()
