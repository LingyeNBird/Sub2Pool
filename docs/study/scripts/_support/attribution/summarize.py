"""Aggregate raw V2 results into auditable summary tables and LaTeX fragments."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

from metrics import cvar, order_stat_interval

ROOT=Path(__file__).resolve().parents[3]
RAW=ROOT/'results/raw';OUT=ROOT/'results/summary';GEN=ROOT/'results/generated'
OUT.mkdir(parents=True,exist_ok=True);GEN.mkdir(parents=True,exist_ok=True)

ALG_LABELS={
'global_proportional':'全周期成本比例','adjacent_integer':'相邻整数跳变','window_5pp':'单一 5 pp 窗口',
'phase_selected':'冻结多相位','adaptive_selected':'冻结自适应多相位','moving_backward':'后视局部',
'moving_centered':'居中局部','moving_forward':'前视局部','tv_minimum_variation':'最小总变差',
'set_midpoint_lex':'集合 minimax-中点层级','set_box_midpoint':'集合坐标中点投影',
}


def read_folder(name):
    files=sorted((RAW/name).glob('*.csv.gz'))
    return pd.concat([pd.read_csv(p) for p in files],ignore_index=True) if files else pd.DataFrame()


def summarize_algorithms(df, tag):
    rows=[]
    for alg,g0 in df.groupby('algorithm'):
        all_runs=len(g0);g=g0[g0.success==True].copy()
        if not len(g):
            rows.append({'algorithm':alg,'label':ALG_LABELS.get(alg,alg),'runs':all_runs,'success_runs':0,'failure_rate':1.0})
            continue
        mae=np.asarray(g.mae,float);mx=np.asarray(g.max_abs,float);ov=np.asarray(g.max_over,float);un=np.asarray(g.max_under,float)
        q95lo,q95hi=order_stat_interval(mx,.95);q99lo,q99hi=order_stat_interval(mx,.99)
        o95lo,o95hi=order_stat_interval(ov,.95);o99lo,o99hi=order_stat_interval(ov,.99)
        mean=float(mae.mean());se=float(mae.std(ddof=1)/np.sqrt(len(mae))) if len(mae)>1 else np.nan
        rows.append({
            'algorithm':alg,'label':ALG_LABELS.get(alg,alg),'runs':all_runs,'success_runs':len(g),'failure_rate':1-len(g)/all_runs,
            'MAE_mean':mean,'MAE_mean_CI_low':mean-1.96*se,'MAE_mean_CI_high':mean+1.96*se,'MAE_median':float(np.median(mae)),
            'RMSE_mean':float(g.rmse.mean()),'max_abs_P95':float(np.quantile(mx,.95)),'max_abs_P95_CI_low':q95lo,'max_abs_P95_CI_high':q95hi,
            'max_abs_P99':float(np.quantile(mx,.99)),'max_abs_P99_CI_low':q99lo,'max_abs_P99_CI_high':q99hi,
            'max_over_P95':float(np.quantile(ov,.95)),'max_over_P95_CI_low':o95lo,'max_over_P95_CI_high':o95hi,
            'max_over_P99':float(np.quantile(ov,.99)),'max_over_P99_CI_low':o99lo,'max_over_P99_CI_high':o99hi,
            'max_under_P95':float(np.quantile(un,.95)),'CVaR95_max_abs':cvar(mx,.95),'CVaR99_max_abs':cvar(mx,.99),
            'observed_max_abs':float(mx.max()),'observed_max_over':float(ov.max()),'total_abs_mean':float(np.mean(np.abs(g.total_error))),
            'wait_mean_hours':float(g.settlement_wait_mean_minutes.mean()/60) if 'settlement_wait_mean_minutes' in g else np.nan,
            'interval_coverage':float(g.interval_coverage_all.dropna().mean()) if 'interval_coverage_all' in g and g.interval_coverage_all.notna().any() else np.nan,
            'interval_width_mean':float(g.interval_width_mean.dropna().mean()) if 'interval_width_mean' in g and g.interval_width_mean.notna().any() else np.nan,
        })
    out=pd.DataFrame(rows).sort_values(['MAE_mean','max_over_P95'],na_position='last')
    out.to_csv(OUT/f'{tag}_summary.csv',index=False)
    return out


def scenario_summary(df,tag):
    ok=df[df.success==True]
    out=ok.groupby(['scenario','algorithm']).agg(
        runs=('seed','count'),MAE_mean=('mae','mean'),max_abs_P95=('max_abs',lambda x:x.quantile(.95)),
        max_over_P95=('max_over',lambda x:x.quantile(.95)),max_abs_P99=('max_abs',lambda x:x.quantile(.99)),observed_max_abs=('max_abs','max')
    ).reset_index()
    out.to_csv(OUT/f'{tag}_scenario_summary.csv',index=False);return out


def paired_bootstrap(df,a,b,metric='mae',reps=1000,seed=20261101):
    x=df[df.algorithm.isin([a,b]) & (df.success==True)].pivot_table(index=['scenario','seed'],columns='algorithm',values=metric,aggfunc='first').dropna()
    if a not in x or b not in x:return {}
    rng=np.random.default_rng(seed);diff=x[a]-x[b]
    observed=float(diff.mean());boot=[]
    groups={s:g.to_numpy(float) for s,g in diff.groupby(level=0)}
    for _ in range(reps):
        vals=[]
        for arr in groups.values(): vals.append(rng.choice(arr,len(arr),replace=True))
        boot.append(float(np.mean(np.concatenate(vals))))
    return {'algorithm_a':a,'algorithm_b':b,'metric':metric,'paired_cycles':len(diff),'mean_difference_a_minus_b':observed,'CI_low':float(np.quantile(boot,.025)),'CI_high':float(np.quantile(boot,.975)),'probability_a_better':float(np.mean(np.array(boot)<0))}


def make_latex_table(summary,path,algorithms=None):
    d=summary.copy()
    if algorithms is not None:d=d[d.algorithm.isin(algorithms)]
    cols=['label','runs','MAE_mean','max_abs_P95','max_abs_P99','max_over_P95','CVaR95_max_abs','failure_rate']
    d=d[cols].copy();d.columns=['方法','周期数','平均 MAE','P95 最大绝对误差','P99 最大绝对误差','P95 最大过算','CVaR95','失败率']
    for c in d.columns[2:]:d[c]=d[c].map(lambda x:'--' if pd.isna(x) else f'{x:.3f}')
    path.write_text(d.to_latex(index=False,escape=True,column_format='lrrrrrrr'),encoding='utf-8')


def main():
    key={}
    fast=read_folder('main_fast')
    if len(fast):
        fs=summarize_algorithms(fast,'main_fast');scenario_summary(fast,'main_fast')
        make_latex_table(fs,GEN/'table_main_fast.tex')
        pairs=[paired_bootstrap(fast,'phase_selected','window_5pp'),paired_bootstrap(fast,'adaptive_selected','phase_selected'),paired_bootstrap(fast,'moving_centered','phase_selected')]
        pd.DataFrame(pairs).to_csv(OUT/'main_fast_paired_bootstrap.csv',index=False)
        key['main_fast_cycles']=int(fast[['scenario','seed']].drop_duplicates().shape[0])
        key['main_fast_rows']=int(len(fast))
    lp=read_folder('main_lp')
    if len(lp):
        ls=summarize_algorithms(lp,'main_lp');scenario_summary(lp,'main_lp');make_latex_table(ls,GEN/'table_main_lp.tex')
        pairs=[paired_bootstrap(lp,'tv_minimum_variation','phase_selected'),paired_bootstrap(lp,'set_midpoint_lex','tv_minimum_variation'),paired_bootstrap(lp,'set_box_midpoint','set_midpoint_lex')]
        pd.DataFrame(pairs).to_csv(OUT/'main_lp_paired_bootstrap.csv',index=False)
        key['main_lp_cycles']=int(lp[['scenario','seed']].drop_duplicates().shape[0])
        key['main_lp_rows']=int(len(lp))
    for tag in ['ood_fast','ood_lp']:
        df=read_folder(tag)
        if len(df):
            s=summarize_algorithms(df,tag);scenario_summary(df,tag);make_latex_table(s,GEN/f'table_{tag}.tex')
            key[tag+'_cycles']=int(df[['scenario','seed']].drop_duplicates().shape[0])
    phase=RAW/'phase'
    if (phase/'selected_phase_configs.json').exists():key['selected_phase']=json.loads((phase/'selected_phase_configs.json').read_text())
    # Audits
    cp=RAW/'audits/center_nonuniqueness.csv.gz'
    if cp.exists():
        d=pd.read_csv(cp);m=d[d.algorithm=='set_midpoint_lex']
        key['center_cycles']=int(len(m));key['center_nonunique_pct']=float(100*np.mean(m.face_diameter_linf>1e-5));key['center_face_median']=float(m.face_diameter_linf.median());key['center_face_p95']=float(m.face_diameter_linf.quantile(.95));key['center_face_max']=float(m.face_diameter_linf.max())
        d.groupby('algorithm').agg(MAE_mean=('mae','mean'),max_over_P95=('max_over',lambda x:x.quantile(.95)),face_diameter_median=('face_diameter_linf','median')).reset_index().to_csv(OUT/'center_audit_summary.csv',index=False)
    tp=RAW/'audits/tie_coupling.csv.gz'
    if tp.exists():
        d=pd.read_csv(tp);key['tie_cycles']=int(len(d));key['tie_exact_coverage']=float(d.exact_coverage.mean());key['tie_legacy_coverage']=float(d.legacy_coverage.mean());key['tie_extra_width_mean']=float(d.legacy_minus_exact_width_mean.mean());key['tie_extra_width_p95']=float(d.legacy_minus_exact_width_mean.quantile(.95));key['tie_extra_width_max']=float(d.legacy_minus_exact_width_mean.max())
        d.describe(include='all').to_csv(OUT/'tie_summary.csv')
    # Auxiliary summaries
    aux=[
        ('delay/online_delay_metrics.csv.gz',['algorithm','requested_lag_minutes'],'delay_summary'),
        ('history/history_metrics.csv.gz',['regime','algorithm'],'history_summary_auto'),
        ('reconciliation/reconciliation_metrics.csv.gz',['algorithm','reconciliation'],'reconciliation_summary'),
        ('boundary/boundary_metrics.csv.gz',['algorithm'],'boundary_summary'),
        ('quantizer/quantizer_metrics.csv.gz',['generator_quantizer','sampling_minutes','algorithm'],'quantizer_summary'),
        ('adversarial/adversarial_search_best.csv',['search_target','algorithm'],'adversarial_summary'),
    ]
    for rel,groups,name in aux:
        p=RAW/rel
        if not p.exists():continue
        d=pd.read_csv(p);ok=d[d.success==True] if 'success' in d else d
        agg={}
        if 'mae' in ok:agg['MAE_mean']=('mae','mean')
        if 'max_abs' in ok:agg['max_abs_P95']=('max_abs',lambda x:x.quantile(.95))
        if 'max_over' in ok:agg['max_over_P95']=('max_over',lambda x:x.quantile(.95))
        if 'revision_from_zero_linf' in ok:agg['revision_P95']=('revision_from_zero_linf',lambda x:x.quantile(.95))
        if agg:ok.groupby(groups).agg(**agg).reset_index().to_csv(OUT/f'{name}.csv',index=False)
    (OUT/'key_values.json').write_text(json.dumps(key,indent=2,ensure_ascii=False),encoding='utf-8')
    # LaTeX macro file with only scalar values; structured configs remain JSON.
    lines=[]
    scalar_map={
        'main_fast_cycles':'MainFastCycles','main_lp_cycles':'MainLPCycles','ood_fast_cycles':'OODFastCycles','ood_lp_cycles':'OODLPCycles',
        'center_cycles':'CenterAuditCycles','center_nonunique_pct':'CenterNonuniquePct','center_face_median':'CenterFaceMedian','center_face_p95':'CenterFaceP95','center_face_max':'CenterFaceMax',
        'tie_cycles':'TieCycles','tie_exact_coverage':'TieExactCoverage','tie_extra_width_mean':'TieExtraWidthMean','tie_extra_width_p95':'TieExtraWidthP95','tie_extra_width_max':'TieExtraWidthMax'}
    for k,macro in scalar_map.items():
        if k in key:
            v=key[k];text=f'{v:.3f}' if isinstance(v,float) else str(v)
            lines.append(f'\\newcommand{{\\{macro}}}{{{text}}}')
    (GEN/'key_values.tex').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':main()
