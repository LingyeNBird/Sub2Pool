"""Reproducible SYNTHETIC sensitivity benchmark. No production data/network."""
import argparse
import json
import os
import sys
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','pinche.settings')
os.environ.setdefault('DJANGO_DEBUG','true')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import django
django.setup()
import numpy as np
from monitor.research.estimator import analyze
from monitor.research.protocol import FAMILIES, METHOD, method_digest
from monitor.tests.research.synthetic import simulate


def benchmark(seeds):
    scenarios=[('unchanged',(1,1,1,1)),('global',(1.8,)*4),('cache_read',(1,1,2,1)),('cache_creation',(1,2,1,1)),('output',(1,1,1,2)),('input',(2,1,1,1)),('mixed',(1.5,1,2,1))]
    rows=[]
    for name,factors in scenarios:
        for drift in (.025,.10,.30):
            for seed in range(1,seeds+1):
                result=analyze(simulate(factors,seed=seed,drift=drift),gateway_only=True)
                rows.append(dict(truth=name,drift=drift,seed=seed,status=result['status'],
                    winner=FAMILIES[int(np.argmax(result['support']))] if result['eligible'] else None,
                    winner_support=max(result['support'])))
    return {'synthetic_only':True,'method':METHOD,'method_digest':method_digest(),
            'seeds_per_scenario':seeds,'cycles_per_simulation':5,'blocks_per_cycle':20,
            'warning':'This benchmark tests a deliberately specified generator; it does not calibrate causal probabilities on real OpenAI subscriptions.',
            'rows':rows}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--seeds',type=int,default=5);parser.add_argument('--output',required=True);args=parser.parse_args()
    if not 1<=args.seeds<=50: parser.error('seeds must be 1..50')
    output=benchmark(args.seeds);Path(args.output).write_text(json.dumps(output,indent=2)+'\n')
    print(f"Synthetic benchmark: {len(output['rows'])} scenarios written; no real-world discovery asserted.")
