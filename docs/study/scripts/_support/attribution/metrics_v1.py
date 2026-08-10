"""Evaluation metrics."""
from __future__ import annotations
import numpy as np
from algorithms import AttributionResult
from models import CycleData

def evaluate(cycle:CycleData,res:AttributionResult)->tuple[dict,list[dict]]:
    true=cycle.true_user_totals
    if not res.success or np.any(~np.isfinite(res.estimate)):
        row={"success":False,"algorithm":res.name,"message":res.message}
        return row,[]
    e=np.asarray(res.estimate)-true
    over=np.maximum(e,0);under=np.maximum(-e,0)
    md=res.metadata or {}
    row={
      "success":True,"algorithm":res.name,"message":res.message,
      "mae":float(np.mean(np.abs(e))),"rmse":float(np.sqrt(np.mean(e**2))),
      "max_abs":float(np.max(np.abs(e))),"max_over":float(np.max(over)),"max_under":float(np.max(under)),
      "mean_bias":float(np.mean(e)),"total_error":float(np.sum(res.estimate)-cycle.true_total),
      "relative_mae_total":float(np.mean(np.abs(e))/max(cycle.true_total,1e-12)),
      "estimated_total":float(np.sum(res.estimate)),
      "phase_dispersion":float(md.get("phase_dispersion_iqr_max",np.nan)),
      "selected_width":float(md.get("selected_width",md.get("width",np.nan))),
      "set_radius":float(md.get("radius",np.nan)),
      "set_unrestricted_radius":float(md.get("minimax_unrestricted_radius",np.nan)),
      "center_certified":md.get("center_temporally_certified",None),
      "n_intervals":float(md.get("n_intervals",np.nan)),
    }
    if res.lower is not None and res.upper is not None:
        cover=(true>=res.lower-1e-7)&(true<=res.upper+1e-7)
        row.update({"interval_coverage_all":bool(np.all(cover)),"interval_width_mean":float(np.mean(res.upper-res.lower)),"interval_width_max":float(np.max(res.upper-res.lower))})
    else:row.update({"interval_coverage_all":np.nan,"interval_width_mean":np.nan,"interval_width_max":np.nan})
    users=[]
    for i in range(cycle.n_users):
        users.append({"user":i,"true_q":float(true[i]),"estimate_q":float(res.estimate[i]),"error":float(e[i]),"abs_error":float(abs(e[i])),"over":float(over[i]),"under":float(under[i]),"lower":float(res.lower[i]) if res.lower is not None else np.nan,"upper":float(res.upper[i]) if res.upper is not None else np.nan})
    return row,users

def cvar(values,alpha=.95):
    x=np.sort(np.asarray(values,float));k=max(0,int(np.floor(alpha*len(x))));return float(np.mean(x[k:])) if len(x) else np.nan
