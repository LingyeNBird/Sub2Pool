"""Chunked simultaneous-event coupling audit with resumable part files."""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from run_study import ROOT, RAW, CFG, _tie_one


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--jobs',type=int,default=4);ap.add_argument('--cycles',type=int,default=None);ap.add_argument('--chunk',type=int,default=100)
    args=ap.parse_args();n=int(args.cycles or CFG['sample_sizes']['tie_cycles'])
    folder=RAW/'audits';parts=folder/'tie_parts';parts.mkdir(parents=True,exist_ok=True)
    ss=np.random.SeedSequence(int(CFG['seeds']['tie']));seeds=[int(x.generate_state(1)[0]) for x in ss.spawn(n)]
    pd.DataFrame({'seed':seeds}).to_csv(folder/'tie_manifest.csv',index=False)
    for start in range(0,n,args.chunk):
        out=parts/f'part_{start:05d}_{min(start+args.chunk,n):05d}.csv.gz'
        if out.exists(): continue
        subset=seeds[start:start+args.chunk]
        rows=Parallel(n_jobs=args.jobs,verbose=8)(delayed(_tie_one)(s) for s in subset)
        pd.DataFrame(rows).to_csv(out,index=False,compression='gzip')
    files=sorted(parts.glob('part_*.csv.gz'))
    data=pd.concat([pd.read_csv(p) for p in files],ignore_index=True)
    if len(data)!=n: raise RuntimeError(f'expected {n}, got {len(data)}')
    data.to_csv(folder/'tie_coupling.csv.gz',index=False,compression='gzip')

if __name__=='__main__':main()
