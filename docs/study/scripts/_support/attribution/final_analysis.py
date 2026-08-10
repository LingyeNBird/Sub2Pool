"""Final statistical analysis, LaTeX fragments, and figures for V2.

All tables are regenerated from raw CSV files.  No reported number is manually
entered into the manuscript.  Monte Carlo maxima are labelled observed maxima.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[3]
RAW=ROOT/'results/raw'; OUT=ROOT/'results/summary'; GEN=ROOT/'results/generated'; FIG=ROOT/'results/figures'
for p in [OUT,GEN,FIG]:p.mkdir(parents=True,exist_ok=True)

LABELS={
'global_proportional':'Global proportional','adjacent_integer':'Adjacent integer','window_5pp':'Single 5 pp',
'phase_selected':'Frozen multiphase','adaptive_selected':'Adaptive multiphase','moving_backward':'Backward local',
'moving_centered':'Centered local','moving_forward':'Forward local','tv_minimum_variation':'Minimum TV',
'set_midpoint_lex':'Set minimax + hierarchy','set_box_midpoint':'Set coordinate midpoint',
'global_delayed':'Global','phase_accuracy_delayed':'Multiphase','phase_tail_delayed':'Tail multiphase',
'adaptive_accuracy_delayed':'Adaptive','adaptive_tail_delayed':'Tail adaptive','moving_backward_delayed':'Backward local',
'moving_centered_delayed':'Centered local','moving_forward_delayed':'Forward local','tv_delayed':'Minimum TV',
}
ZH={
'global_proportional':'全周期成本比例','adjacent_integer':'相邻整数跳变','window_5pp':'单一 5 pp 窗口',
'phase_selected':'冻结多相位','adaptive_selected':'冻结自适应多相位','moving_backward':'后视局部',
'moving_centered':'居中局部','moving_forward':'前视局部','tv_minimum_variation':'最小总变差',
'set_midpoint_lex':'精确集合 minimax--层级点','set_box_midpoint':'集合坐标中点投影',
}

def read_parts(folder):
    files=sorted((RAW/folder).glob('*.csv.gz'))
    return pd.concat([pd.read_csv(p) for p in files],ignore_index=True) if files else pd.DataFrame()

def fmt(x,d=3):
    if x is None or (isinstance(x,float) and not np.isfinite(x)):return '--'
    return f'{float(x):.{d}f}'

def latex_escape(s):
    return str(s).replace('&','\\&').replace('_','\\_').replace('%','\\%')

def write_tabular(path, headers, rows, align=None, resize=False, small=True):
    align=align or ('l'+'r'*(len(headers)-1))
    lines=[]
    if resize: lines.append('\\resizebox{\\textwidth}{!}{%')
    lines.append(f'\\begin{{tabular}}{{{align}}}')
    lines.append('\\toprule')
    lines.append(' & '.join(headers)+r' \\')
    lines.append('\\midrule')
    for row in rows: lines.append(' & '.join(map(str,row))+r' \\')
    lines.append('\\bottomrule')
    lines.append('\\end{tabular}')
    if resize: lines.append('}')
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')

def algorithm_summary(d):
    rows=[]
    for alg,g0 in d.groupby('algorithm'):
        ok=g0[g0.success==True]
        if len(ok)==0:
            rows.append(dict(algorithm=alg,runs=len(g0),failure_rate=1.0));continue
        rows.append(dict(
            algorithm=alg,runs=len(g0),success_runs=len(ok),failure_rate=1-len(ok)/len(g0),
            mae=float(ok.mae.mean()),mae_se=float(ok.mae.std(ddof=1)/np.sqrt(len(ok))),
            p95_abs=float(ok.max_abs.quantile(.95)),p99_abs=float(ok.max_abs.quantile(.99)),
            p95_over=float(ok.max_over.quantile(.95)),p99_over=float(ok.max_over.quantile(.99)),
            cvar95_abs=float(ok.max_abs[ok.max_abs>=ok.max_abs.quantile(.95)].mean()),
            observed_max_abs=float(ok.max_abs.max()),observed_max_over=float(ok.max_over.max()),
            wait_h=float(ok.settlement_wait_mean_minutes.mean()/60) if 'settlement_wait_mean_minutes' in ok and ok.settlement_wait_mean_minutes.notna().any() else np.nan,
            interval_coverage=float(ok.interval_coverage_all.dropna().mean()) if 'interval_coverage_all' in ok and ok.interval_coverage_all.notna().any() else np.nan,
            interval_width=float(ok.interval_width_mean.dropna().mean()) if 'interval_width_mean' in ok and ok.interval_width_mean.notna().any() else np.nan,
            set_radius=float(ok.set_radius.dropna().mean()) if 'set_radius' in ok and ok.set_radius.notna().any() else np.nan,
        ))
    return pd.DataFrame(rows).sort_values('mae',na_position='last')

def paired_mean(d,a,b,metric='mae'):
    p=d[d.algorithm.isin([a,b]) & (d.success==True)].pivot(index=['scenario','seed'],columns='algorithm',values=metric).dropna()
    x=p[a]-p[b];m=float(x.mean());se=float(x.std(ddof=1)/np.sqrt(len(x)))
    return dict(a=a,b=b,n=len(x),difference=m,lo=m-1.96*se,hi=m+1.96*se)

def table_summary(summary,path,include_wait=False):
    headers=['方法','周期','平均 MAE','P95 最大绝对','P99 最大绝对','P95 过算','CVaR95','观察最大']
    if include_wait:headers.append('平均上下文等待/h')
    rows=[]
    for r in summary.itertuples():
        row=[latex_escape(ZH.get(r.algorithm,r.algorithm)),f'{int(r.runs):,}',fmt(r.mae),fmt(r.p95_abs),fmt(r.p99_abs),fmt(r.p95_over),fmt(r.cvar95_abs),fmt(r.observed_max_abs)]
        if include_wait:row.append(fmt(r.wait_h,1))
        rows.append(row)
    write_tabular(path,headers,rows,resize=True)

def savefig(name):
    plt.tight_layout();plt.savefig(FIG/f'{name}.pdf',bbox_inches='tight');plt.savefig(FIG/f'{name}.png',dpi=180,bbox_inches='tight');plt.close()

def main():
    key={}
    datasets={tag:read_parts(tag) for tag in ['main_fast','main_lp','ood_fast','ood_lp']}
    summaries={}
    for tag,d in datasets.items():
        s=algorithm_summary(d);summaries[tag]=s;s.to_csv(OUT/f'{tag}_final_summary.csv',index=False)
        d[d.success==True].groupby(['scenario','algorithm']).agg(MAE=('mae','mean'),P95_abs=('max_abs',lambda x:x.quantile(.95)),P95_over=('max_over',lambda x:x.quantile(.95)),P99_over=('max_over',lambda x:x.quantile(.99)),Observed_max=('max_abs','max')).reset_index().to_csv(OUT/f'{tag}_final_scenarios.csv',index=False)
        key[f'{tag}_cycles']=int(d[['scenario','seed']].drop_duplicates().shape[0])
        table_summary(s,GEN/f'table_{tag}_final.tex',include_wait=('fast' in tag))
    # Intrinsic set diagnostics
    mlp=datasets['main_lp'];setd=mlp[mlp.algorithm=='set_midpoint_lex']
    key.update({
        'set_coverage':float(setd.interval_coverage_all.mean()),'set_width_mean':float(setd.interval_width_mean.mean()),
        'set_width_p95':float(setd.interval_width_mean.quantile(.95)),'set_radius_mean':float(setd.set_radius.mean()),
        'set_radius_p95':float(setd.set_radius.quantile(.95)),'set_radius_max':float(setd.set_radius.max()),
    })
    # Principal values
    for tag,s in summaries.items():
        for alg in s.algorithm:
            r=s[s.algorithm==alg].iloc[0]
            stem=f'{tag}_{alg}'
            for col in ['mae','p95_abs','p99_abs','p95_over','p99_over','observed_max_abs','wait_h']:
                key[f'{stem}_{col}']=float(r[col]) if pd.notna(r[col]) else None
    key['phase_vs_window_mae_improvement_pct']=100*(key['main_fast_window_5pp_mae']-key['main_fast_phase_selected_mae'])/key['main_fast_window_5pp_mae']
    key['phase_vs_window_p95_over_improvement_pct']=100*(key['main_fast_window_5pp_p95_over']-key['main_fast_phase_selected_p95_over'])/key['main_fast_window_5pp_p95_over']
    paired=[paired_mean(datasets['main_fast'],'phase_selected','window_5pp'),paired_mean(datasets['main_fast'],'adjacent_integer','phase_selected'),paired_mean(datasets['main_lp'],'tv_minimum_variation','phase_selected'),paired_mean(datasets['main_lp'],'set_midpoint_lex','tv_minimum_variation'),paired_mean(datasets['main_lp'],'set_box_midpoint','set_midpoint_lex')]
    pd.DataFrame(paired).to_csv(OUT/'paired_mean_differences.csv',index=False)
    write_tabular(GEN/'table_paired.tex',['A--B','配对周期','平均 MAE 差','95\\% 区间'],[[latex_escape(f"{ZH.get(x['a'],x['a'])} -- {ZH.get(x['b'],x['b'])}"),f"{x['n']:,}",fmt(x['difference']),f"[{fmt(x['lo'])}, {fmt(x['hi'])}]"] for x in paired],resize=True)

    # Center audit
    center=pd.read_csv(RAW/'audits/center_nonuniqueness.csv.gz')
    cbase=center[center.algorithm=='set_midpoint_lex']
    key.update(center_cycles=int(len(cbase)),center_nonunique_pct=float(100*np.mean(cbase.face_diameter_linf>1e-5)),center_face_median=float(cbase.face_diameter_linf.median()),center_face_p95=float(cbase.face_diameter_linf.quantile(.95)),center_face_max=float(cbase.face_diameter_linf.max()))
    cs=center.groupby('algorithm').agg(cycles=('seed','count'),MAE=('mae','mean'),P95_abs=('max_abs',lambda x:x.quantile(.95)),P95_over=('max_over',lambda x:x.quantile(.95))).reset_index()
    cs.to_csv(OUT/'center_final_summary.csv',index=False)
    write_tabular(GEN/'table_center.tex',['点规则','周期','平均 MAE','P95 最大绝对','P95 过算'],[[latex_escape(a),f'{int(n):,}',fmt(m),fmt(pa),fmt(po)] for a,n,m,pa,po in cs.itertuples(index=False,name=None)],resize=True)
    plt.figure(figsize=(6.2,4.0));plt.hist(cbase.face_diameter_linf,bins=35);plt.xlabel('Primary minimax face coordinate diameter (pp)');plt.ylabel('Cycles');savefig('center_face_hist')

    # Timestamp ties
    ties=pd.read_csv(RAW/'audits/tie_coupling.csv.gz')
    key.update(tie_cycles=len(ties),tie_exact_coverage=float(ties.exact_coverage.mean()),tie_legacy_coverage=float(ties.legacy_coverage.mean()),tie_extra_mean=float(ties.legacy_minus_exact_width_mean.mean()),tie_extra_p95=float(ties.legacy_minus_exact_width_mean.quantile(.95)),tie_extra_max=float(ties.legacy_minus_exact_width_mean.max()))
    write_tabular(GEN/'table_ties.tex',['周期','精确集合覆盖','旧外包络覆盖','平均额外宽度/pp','P95 额外宽度','最大额外宽度'],[[f'{len(ties):,}',fmt(ties.exact_coverage.mean()),fmt(ties.legacy_coverage.mean()),fmt(ties.legacy_minus_exact_width_mean.mean()),fmt(ties.legacy_minus_exact_width_mean.quantile(.95)),fmt(ties.legacy_minus_exact_width_mean.max())]],resize=True)
    plt.figure(figsize=(5.8,5.0));plt.scatter(ties.exact_width_mean,ties.legacy_width_mean,s=8,alpha=.35);lim=max(ties.legacy_width_mean.max(),ties.exact_width_mean.max());plt.plot([0,lim],[0,lim],linestyle='--');plt.xlabel('Exact shared-time width (pp)');plt.ylabel('Legacy independent-box width (pp)');savefig('timestamp_tie_scatter')

    # Phase design and full interaction
    selected=json.loads((RAW/'phase/selected_phase_configs.json').read_text())
    key['selected_phase']=selected
    ps=pd.read_csv(RAW/'phase/phase_screen_summary.csv')
    bestcount=ps.groupby('n_phases').MAE_mean.min().reset_index()
    plt.figure(figsize=(6.2,4.0));plt.plot(bestcount.n_phases,bestcount.MAE_mean,marker='o');plt.xlabel('Number of phases');plt.ylabel('Best development MAE (pp)');savefig('phase_count')
    sv=pd.read_csv(RAW/'phase/static_validation_summary.csv')
    bestwidth=sv.groupby('width').agg(MAE=('MAE_mean','min'),Wait=('wait_mean','min')).reset_index()
    plt.figure(figsize=(6.2,4.0));plt.plot(bestwidth.width,bestwidth.MAE,marker='o');plt.xlabel('Window width (pp)');plt.ylabel('Best validation MAE (pp)');savefig('phase_width_mae')
    plt.figure(figsize=(6.2,4.0));plt.plot(bestwidth.width,bestwidth.Wait/60,marker='o');plt.xlabel('Window width (pp)');plt.ylabel('Minimum mean context time (hours)');savefig('phase_width_wait')
    selrows=[]
    for name,cfg in selected.items():
        if not isinstance(cfg,dict):continue
        selrows.append([latex_escape(name),fmt(cfg.get('width',cfg.get('max_width',np.nan)),1),str(cfg.get('n_phases','--')),latex_escape(cfg.get('phase_scheme','--')),latex_escape(cfg.get('aggregation','--')),fmt(cfg.get('threshold',np.nan))])
    write_tabular(GEN/'table_selected_configs.tex',['操作点','宽度/最大宽度','相位数','偏移','聚合','阈值'],selrows,resize=True)
    inter=pd.read_csv(RAW/'phase_interaction/interaction_summary.csv')
    ibest=inter.sort_values('MAE_mean').groupby('coordinate',as_index=False).head(1).sort_values('MAE_mean')
    selectedrow=inter[(inter.coordinate=='proxy_progress')&(inter.width==selected['static_accuracy']['width'])&(inter.n_phases==selected['static_accuracy']['n_phases'])&(inter.phase_scheme==selected['static_accuracy']['phase_scheme'])&(inter.aggregation==selected['static_accuracy']['aggregation'])]
    ic=pd.concat([ibest.assign(row_type='coordinate best'),selectedrow.assign(row_type='frozen selected')],ignore_index=True)
    ic.to_csv(OUT/'interaction_coordinate_best.csv',index=False)
    write_tabular(GEN/'table_interaction_coordinate.tex',['类别','坐标','宽度','相位','偏移','聚合','平均 MAE','P95 过算','上下文/h'],[[latex_escape(r.row_type),latex_escape(r.coordinate),fmt(r.width,1),str(int(r.n_phases)),latex_escape(r.phase_scheme),latex_escape(r.aggregation),fmt(r.MAE_mean),fmt(r.max_over_P95),fmt(r.wait_mean_minutes/60,1)] for r in ic.itertuples()],resize=True)
    plt.figure(figsize=(6.8,4.2));tmp=ibest.sort_values('MAE_mean');plt.bar(tmp.coordinate,tmp.MAE_mean);plt.ylabel('Best audit MAE (pp)');plt.xticks(rotation=25,ha='right');savefig('interaction_coordinate')

    # Delay
    delay=pd.read_csv(RAW/'delay/online_delay_metrics.csv.gz')
    dsum=delay.groupby(['algorithm','requested_lag_minutes']).agg(targets=('seed','count'),MAE=('mae','mean'),P95_abs=('max_abs',lambda x:x.quantile(.95)),P95_over=('max_over',lambda x:x.quantile(.95)),Revision_P95=('revision_from_zero_linf',lambda x:x.quantile(.95)),Actual_lag=('actual_lag_minutes','mean')).reset_index()
    dsum.to_csv(OUT/'delay_final_summary.csv',index=False)
    focus=['tv_delayed','phase_accuracy_delayed','adaptive_accuracy_delayed','moving_backward_delayed','moving_centered_delayed','global_delayed']
    tab=dsum[dsum.algorithm.isin(focus)&dsum.requested_lag_minutes.isin([0,1440,2880])]
    write_tabular(GEN/'table_delay.tex',['方法','请求等待/h','实际等待/h','目标前缀数','平均 MAE','P95 过算','相对零等待修正 P95'],[[latex_escape(LABELS.get(r.algorithm,r.algorithm)),fmt(r.requested_lag_minutes/60,0),fmt(r.Actual_lag/60,1),f'{int(r.targets):,}',fmt(r.MAE),fmt(r.P95_over),fmt(r.Revision_P95)] for r in tab.sort_values(['algorithm','requested_lag_minutes']).itertuples()],resize=True)
    for metric,name,ylabel in [('MAE','delay_mae','Mean target-prefix MAE (pp)'),('P95_over','delay_p95_over','P95 maximum over-attribution (pp)')]:
        plt.figure(figsize=(7.0,4.6))
        for alg in focus:
            q=dsum[dsum.algorithm==alg].sort_values('requested_lag_minutes');plt.plot(q.requested_lag_minutes/60,q[metric],marker='o',label=LABELS.get(alg,alg))
        plt.xlabel('Requested decision lag (hours)');plt.ylabel(ylabel);plt.legend(fontsize=7,ncol=2);savefig(name)

    # Quantizer stress
    quant=pd.read_csv(RAW/'quantizer/quantizer_metrics.csv.gz')
    qsum=quant.groupby(['generator_quantizer','algorithm']).agg(cycles=('seed','count'),success=('success','mean'),MAE=('mae','mean'),P95_over=('max_over',lambda x:x.dropna().quantile(.95))).reset_index()
    qsum.to_csv(OUT/'quantizer_final_summary.csv',index=False)
    qfocus=qsum[qsum.algorithm.isin(['global_proportional','phase_selected','tv_fixed_offset_model','set_fixed_offset_model'])]
    write_tabular(GEN/'table_quantizer.tex',['生成量化器','方法','周期','可行率','平均 MAE','P95 过算'],[[latex_escape(r.generator_quantizer),latex_escape(r.algorithm),f'{int(r.cycles):,}',fmt(r.success),fmt(r.MAE),fmt(r.P95_over)] for r in qfocus.itertuples()],resize=True)
    pivot=qsum[qsum.algorithm.isin(['tv_fixed_offset_model','set_fixed_offset_model'])].pivot(index='generator_quantizer',columns='algorithm',values='success')
    plt.figure(figsize=(6.6,4.2));x=np.arange(len(pivot));w=.38
    for j,c in enumerate(pivot.columns):plt.bar(x+(j-.5)*w,pivot[c],width=w,label=c)
    plt.xticks(x,pivot.index,rotation=20,ha='right');plt.ylabel('Fixed-offset model feasibility rate');plt.ylim(0,1.05);plt.legend(fontsize=8);savefig('quantizer_feasibility')
    key['irregular_set_feasibility']=float(qsum[(qsum.generator_quantizer=='irregular')&(qsum.algorithm=='set_fixed_offset_model')].success.iloc[0])
    key['switching_set_feasibility']=float(qsum[(qsum.generator_quantizer=='switching_offset')&(qsum.algorithm=='set_fixed_offset_model')].success.iloc[0])

    # History
    hist=pd.read_csv(RAW/'history/history_metrics.csv.gz')
    hsum=hist.groupby(['regime','algorithm']).agg(cycles=('cycle_index','count'),MAE=('mae','mean'),P95_over=('max_over',lambda x:x.quantile(.95)),failure=('success',lambda x:1-x.mean())).reset_index()
    hsum.to_csv(OUT/'history_final_summary.csv',index=False)
    write_tabular(GEN/'table_history.tex',['机制','方法','周期','平均 MAE','P95 过算','失败率'],[[latex_escape(r.regime),latex_escape(r.algorithm),f'{int(r.cycles):,}',fmt(r.MAE,4),fmt(r.P95_over,4),fmt(r.failure)] for r in hsum.itertuples()],resize=True)
    plt.figure(figsize=(6.8,4.2));
    for alg in hsum.algorithm.unique():
        q=hsum[hsum.algorithm==alg];plt.plot(q.regime,q.MAE,marker='o',label=alg)
    plt.ylabel('Mean MAE (pp)');plt.legend(fontsize=8);savefig('history_prior')

    # Reconciliation
    rec=pd.read_csv(RAW/'reconciliation/reconciliation_metrics.csv.gz')
    rsum=rec.groupby(['algorithm','reconciliation']).agg(cycles=('seed','count'),MAE=('mae','mean'),P95_abs=('max_abs',lambda x:x.quantile(.95)),P95_over=('max_over',lambda x:x.quantile(.95))).reset_index()
    rsum.to_csv(OUT/'reconciliation_final_summary.csv',index=False)
    write_tabular(GEN/'table_reconciliation.tex',['方法','协调','周期','平均 MAE','P95 最大绝对','P95 过算'],[[latex_escape(r.algorithm),latex_escape(r.reconciliation),f'{int(r.cycles):,}',fmt(r.MAE),fmt(r.P95_abs),fmt(r.P95_over)] for r in rsum.itertuples()],resize=True)

    # Saturation boundary
    bd=pd.read_csv(RAW/'boundary/boundary_metrics.csv.gz')
    bsum=bd.groupby('algorithm').agg(cycles=('seed','count'),success=('success','mean'),MAE=('mae','mean'),P95_abs=('max_abs',lambda x:x.quantile(.95)),P95_over=('max_over',lambda x:x.quantile(.95)),hidden_mean=('hidden_above_100','mean')).reset_index()
    bsum.to_csv(OUT/'boundary_final_summary.csv',index=False)
    write_tabular(GEN/'table_boundary.tex',['方法','周期','成功率','平均隐藏超额','平均 MAE','P95 最大绝对','P95 过算'],[[latex_escape(r.algorithm),f'{int(r.cycles):,}',fmt(r.success),fmt(r.hidden_mean),fmt(r.MAE),fmt(r.P95_abs),fmt(r.P95_over)] for r in bsum.itertuples()],resize=True)

    # Active adversarial search
    adv=pd.read_csv(RAW/'adversarial/adversarial_search_best.csv')
    asu=adv.groupby(['search_target','algorithm']).agg(candidates=('restart','count'),max_over_found=('max_over','max'),max_abs_found=('max_abs','max'),MAE_mean=('mae','mean')).reset_index()
    asu.to_csv(OUT/'adversarial_final_summary.csv',index=False)
    target_alg={'global':'global_proportional','phase':'phase_selected','adaptive':'adaptive_selected','moving':'moving_selected'}
    atr=[]
    for target,alg in target_alg.items():
        r=asu[(asu.search_target==target)&(asu.algorithm==alg)].iloc[0];atr.append([target,latex_escape(alg),f'{int(r.candidates):,}',fmt(r.max_over_found),fmt(r.max_abs_found),fmt(r.MAE_mean)])
    write_tabular(GEN/'table_adversarial.tex',['搜索目标','被优化方法','重启','发现最大过算','发现最大绝对','候选平均 MAE'],atr,resize=True)
    plt.figure(figsize=(6.2,4.2));aa=pd.DataFrame(atr,columns=['target','alg','n','over','abs','mae']);plt.bar(aa.target,aa.over.astype(float));plt.ylabel('Largest discovered over-attribution (pp)');savefig('adversarial_found')

    # Main and OOD plots
    for tag in ['main_fast','main_lp','ood_fast','ood_lp']:
        s=summaries[tag].sort_values('mae')
        plt.figure(figsize=(7.4,4.6));plt.barh([LABELS.get(x,x) for x in s.algorithm],s.mae);plt.xlabel('Mean participant MAE (pp)');savefig(f'{tag}_mae')
        scen=pd.read_csv(OUT/f'{tag}_final_scenarios.csv')
        pivot=scen.pivot(index='scenario',columns='algorithm',values='P95_over')
        alg_order=[a for a in s.algorithm if a in pivot.columns];pivot=pivot[alg_order]
        plt.figure(figsize=(9.0,5.0));im=plt.imshow(pivot.to_numpy(),aspect='auto');plt.colorbar(im,label='P95 max over-attribution (pp)');plt.yticks(np.arange(len(pivot)),pivot.index);plt.xticks(np.arange(len(pivot.columns)),[LABELS.get(a,a) for a in pivot.columns],rotation=35,ha='right');savefig(f'{tag}_scenario_heatmap')

    # Key-value macros and JSON
    key_path=OUT/'final_key_values.json';key_path.write_text(json.dumps(key,ensure_ascii=False,indent=2),encoding='utf-8')
    macro={
        'MainFastCycles':key['main_fast_cycles'],'MainLPCycles':key['main_lp_cycles'],'OODFastCycles':key['ood_fast_cycles'],'OODLPCycles':key['ood_lp_cycles'],
        'CenterCycles':key['center_cycles'],'CenterNonuniquePct':key['center_nonunique_pct'],'CenterFaceMedian':key['center_face_median'],'CenterFacePNinetyFive':key['center_face_p95'],'CenterFaceMax':key['center_face_max'],
        'TieCycles':key['tie_cycles'],'TieExtraMean':key['tie_extra_mean'],'TieExtraPNinetyFive':key['tie_extra_p95'],'TieExtraMax':key['tie_extra_max'],
        'SetCoveragePct':100*key['set_coverage'],'SetWidthMean':key['set_width_mean'],'SetWidthPNinetyFive':key['set_width_p95'],'SetRadiusMean':key['set_radius_mean'],'SetRadiusPNinetyFive':key['set_radius_p95'],
        'PhaseMAE':key['main_fast_phase_selected_mae'],'WindowFiveMAE':key['main_fast_window_5pp_mae'],'PhaseImprovementPct':key['phase_vs_window_mae_improvement_pct'],
        'AdjacentMAE':key['main_fast_adjacent_integer_mae'],'TVMAE':key['main_lp_tv_minimum_variation_mae'],'TVPOver':key['main_lp_tv_minimum_variation_p95_over'],
        'SetMAE':key['main_lp_set_midpoint_lex_mae'],'SetPOver':key['main_lp_set_midpoint_lex_p95_over'],
        'IrregularFeasiblePct':100*key['irregular_set_feasibility'],'SwitchingFeasiblePct':100*key['switching_set_feasibility'],
    }
    lines=[]
    for name,val in macro.items():
        if isinstance(val,(int,np.integer)):s=f'{int(val):,}'
        else:s=f'{float(val):.3f}'
        lines.append(f'\\newcommand{{\\{name}}}{{{s}}}')
    cfg=selected['static_accuracy'];ad=selected['adaptive_accuracy']
    lines.extend([
        f"\\newcommand{{\\SelectedWidth}}{{{cfg['width']:.0f}}}",f"\\newcommand{{\\SelectedPhases}}{{{cfg['n_phases']}}}",
        f"\\newcommand{{\\SelectedPhaseScheme}}{{{latex_escape(cfg['phase_scheme'])}}}",f"\\newcommand{{\\SelectedAggregation}}{{{latex_escape(cfg['aggregation'])}}}",
        f"\\newcommand{{\\AdaptiveThreshold}}{{{ad['threshold']:.2f}}}",f"\\newcommand{{\\AdaptiveMaxWidth}}{{{ad['max_width']}}}",
    ])
    (GEN/'final_key_values.tex').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':main()
