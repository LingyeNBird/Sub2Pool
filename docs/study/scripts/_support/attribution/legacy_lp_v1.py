"""Sparse linear-programming estimators and identification regions."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from scipy.optimize import linprog
from scipy import sparse
from models import CycleData, aggregate_cycle, AggregatedCycle
from simulate import X_MIN, X_MAX
EPS=1e-8

@dataclass
class LPResult:
    success: bool
    message: str
    estimate: np.ndarray|None=None
    lower: np.ndarray|None=None
    upper: np.ndarray|None=None
    radius: float|None=None
    theta: float|None=None
    total_interval: tuple[float,float]|None=None
    objective: float|None=None
    metadata: dict|None=None

def _solve(c,A,b,bounds,Aeq=None,beq=None):
    return linprog(c,A_ub=A,b_ub=b,A_eq=Aeq,b_eq=beq,bounds=bounds,method="highs",options={"presolve":True})

def _D(K):
    return sparse.diags([np.ones(K),-np.ones(max(K-1,0))],[0,-1],shape=(K,K),format="csr")

def _tb(theta):
    if theta is None:return (0.0,1.0-EPS)
    x=float(np.clip(theta,0,1-EPS));return (x,x)

def _qrows(selector,z,tail=0,slack=0.0,cap=100):
    K=len(z); ap=sparse.hstack([selector,sparse.csr_matrix(np.ones((K,1))),sparse.csr_matrix((K,tail))],format="csr")
    rows=[-ap];rhs=[-(z.astype(float)-slack)];m=z<cap
    if np.any(m):rows.append(ap[m]);rhs.append(z[m].astype(float)+1+slack-EPS)
    return rows,rhs

def total_interval_from_agg(agg,known_theta=None,obs_slack=0.0):
    C=agg.total_costs;K=len(C)
    if K==0:return (0.0,0.0)
    inc=sparse.hstack([_D(K),sparse.csr_matrix((K,1))],format="csr")
    rows=[inc,-inc];rhs=[C*X_MAX,-C*X_MIN]
    qr,qb=_qrows(sparse.eye(K,format="csr"),agg.observed_z,slack=obs_slack);rows+=qr;rhs+=qb
    A=sparse.vstack(rows,format="csr");b=np.concatenate(rhs);up=float(C.sum()*X_MAX)
    bounds=[(0,up)]*K+[_tb(known_theta)];c=np.zeros(K+1);c[K-1]=1
    lo=_solve(c,A,b,bounds);hi=_solve(-c,A,b,bounds)
    return (float(lo.fun),float(-hi.fun)) if lo.success and hi.success else None

def total_interval(cycle,known_theta=None,obs_slack=0.0):
    return total_interval_from_agg(aggregate_cycle(cycle),known_theta,obs_slack)

def _single_endpoint(agg,user,known_theta,obs_slack):
    C=agg.costs_by_interval_user;K,n=C.shape;ct=C[:,user];co=C.sum(1)-ct;D=_D(K);Z=sparse.csr_matrix((K,K));T=sparse.csr_matrix((K,1))
    ia=sparse.hstack([D,Z,T],format="csr");ib=sparse.hstack([Z,D,T],format="csr")
    rows=[ia,-ia,ib,-ib];rhs=[ct*X_MAX,-ct*X_MIN,co*X_MAX,-co*X_MIN]
    sel=sparse.hstack([sparse.eye(K,format="csr"),sparse.eye(K,format="csr")],format="csr")
    qr,qb=_qrows(sel,agg.observed_z,slack=obs_slack);rows+=qr;rhs+=qb
    A=sparse.vstack(rows,format="csr");b=np.concatenate(rhs)
    bounds=[(0,float(ct.sum()*X_MAX))]*K+[(0,float(co.sum()*X_MAX))]*K+[_tb(known_theta)]
    c=np.zeros(2*K+1);c[K-1]=1;return A,b,bounds,c

def _project_simplex(v,z):
    v=np.asarray(v,float)
    if z<=0:return np.zeros_like(v)
    u=np.sort(v)[::-1];css=np.cumsum(u)-z;ind=np.arange(1,len(v)+1);m=u-css/ind>0
    if not np.any(m):return np.full_like(v,z/len(v))
    rho=ind[m][-1];th=css[m][-1]/rho;return np.maximum(v-th,0)

def _full_problem(agg,known_theta,obs_slack):
    C=agg.costs_by_interval_user;K,n=C.shape
    D=sparse.kron(_D(K),sparse.eye(n,format="csr"),format="csr");inc=sparse.hstack([D,sparse.csr_matrix((K*n,1))],format="csr")
    rows=[inc,-inc];rhs=[(C*X_MAX).ravel(),-(C*X_MIN).ravel()]
    sel=sparse.kron(sparse.eye(K,format="csr"),np.ones((1,n)),format="csr")
    qr,qb=_qrows(sel,agg.observed_z,slack=obs_slack);rows+=qr;rhs+=qb
    A=sparse.vstack(rows,format="csr");b=np.concatenate(rhs);uu=C.sum(0)*X_MAX
    bounds=[]
    for _ in range(K):bounds.extend([(0,float(uu[i])) for i in range(n)])
    bounds.append(_tb(known_theta));return A,b,bounds

def identification_region(cycle,known_theta=None,obs_slack=0.0,exact_feasible_center=True):
    agg=aggregate_cycle(cycle);K,n=agg.costs_by_interval_user.shape
    if K==0:
        z=np.zeros(n);return LPResult(True,"empty",z,z,z,0,None,(0,0),0,{"n_intervals":0})
    L=np.empty(n);U=np.empty(n)
    for i in range(n):
        A,b,bd,c=_single_endpoint(agg,i,known_theta,obs_slack);lo=_solve(c,A,b,bd);hi=_solve(-c,A,b,bd)
        if not lo.success or not hi.success:return LPResult(False,f"endpoint infeasible user {i}",metadata={"n_intervals":K})
        L[i]=lo.fun;U[i]=-hi.fun
    ti=total_interval_from_agg(agg,known_theta,obs_slack)
    if ti is None:return LPResult(False,"total infeasible",lower=L,upper=U)
    midtotal=.5*(ti[0]+ti[1]);unr=float(np.max((U-L)/2))
    if not exact_feasible_center:
        q=_project_simplex(.5*(L+U),midtotal);rad=float(np.max(np.maximum(q-L,U-q)))
        return LPResult(True,"projected_midpoint",q,L,U,rad,None,ti,None,{"n_intervals":K,"minimax_unrestricted_radius":unr,"center_temporally_certified":False})
    A,b,bd=_full_problem(agg,known_theta,obs_slack);nq=K*n
    A0=sparse.hstack([A,sparse.csr_matrix((A.shape[0],1))],format="csr");er=np.zeros((2*n,nq+2));eb=np.empty(2*n);base=(K-1)*n
    for i in range(n):
        er[2*i,base+i]=1;er[2*i,-1]=-1;eb[2*i]=L[i]
        er[2*i+1,base+i]=-1;er[2*i+1,-1]=-1;eb[2*i+1]=-U[i]
    A2=sparse.vstack([A0,sparse.csr_matrix(er)],format="csr");b2=np.r_[b,eb];c=np.zeros(nq+2);c[-1]=1
    res=_solve(c,A2,b2,bd+[(0,None)])
    if not res.success:
        q=_project_simplex(.5*(L+U),midtotal);rad=float(np.max(np.maximum(q-L,U-q)))
        return LPResult(True,"center_fallback",q,L,U,rad,None,ti,None,{"n_intervals":K,"minimax_unrestricted_radius":unr,"center_temporally_certified":False})
    q=res.x[base:base+n]
    return LPResult(True,"ok",q,L,U,float(res.x[-1]),float(res.x[nq]),ti,float(res.fun),{"n_intervals":K,"minimax_unrestricted_radius":unr,"center_temporally_certified":True})

def _tv_problem(agg,known_theta,obs_slack):
    C=agg.total_costs;K=len(C);nd=max(K-1,0);nv=2*K+1+nd
    Aeq=sparse.hstack([-sparse.diags(C,format="csr"),_D(K),sparse.csr_matrix((K,1+nd))],format="csr");beq=np.zeros(K)
    sel=sparse.hstack([sparse.csr_matrix((K,K)),sparse.eye(K,format="csr")],format="csr")
    rows,rhs=_qrows(sel,agg.observed_z,tail=nd,slack=obs_slack)
    if nd:
        tv=sparse.lil_matrix((2*nd,nv))
        for k in range(nd):
            dc=2*K+1+k;tv[2*k,k+1]=1;tv[2*k,k]=-1;tv[2*k,dc]=-1;tv[2*k+1,k+1]=-1;tv[2*k+1,k]=1;tv[2*k+1,dc]=-1
        rows.append(tv.tocsr());rhs.append(np.zeros(2*nd))
    A=sparse.vstack(rows,format="csr");b=np.concatenate(rhs);pup=float(C.sum()*X_MAX)
    bd=[(X_MIN,X_MAX)]*K+[(0,pup)]*K+[_tb(known_theta)]+[(0,None)]*nd
    return A,b,bd,Aeq,beq,K,nd,nv

def tv_rate_estimate(cycle,known_theta=None,obs_slack=0.0,prior=None,prior_weight=0.0):
    agg=aggregate_cycle(cycle);A,b,bd,Aeq,beq,K,nd,nv=_tv_problem(agg,known_theta,obs_slack)
    if K==0:return LPResult(False,"no intervals")
    prior=float(np.clip(.5*(X_MIN+X_MAX) if prior is None else prior,X_MIN,X_MAX));C=agg.total_costs;w=C/max(C.sum(),1e-12)
    def with_abs(A0,b0,bd0):
        Ae=sparse.hstack([A0,sparse.csr_matrix((A0.shape[0],K))],format="csr");ar=sparse.lil_matrix((2*K,nv+K));ab=np.empty(2*K)
        for k in range(K):
            ac=nv+k;ar[2*k,k]=1;ar[2*k,ac]=-1;ab[2*k]=prior;ar[2*k+1,k]=-1;ar[2*k+1,ac]=-1;ab[2*k+1]=-prior
        return sparse.vstack([Ae,ar.tocsr()],format="csr"),np.r_[b0,ab],bd0+[(0,None)]*K,sparse.hstack([Aeq,sparse.csr_matrix((K,K))],format="csr")
    if prior_weight<=0:
        c=np.zeros(nv);c[2*K+1:]=1;s1=_solve(c,A,b,bd,Aeq,beq)
        if not s1.success:return LPResult(False,"tv infeasible",metadata={"n_intervals":K})
        At,bt=A,b
        if nd:
            row=np.zeros(nv);row[2*K+1:]=1;At=sparse.vstack([A,sparse.csr_matrix(row[None,:])]);bt=np.r_[b,s1.fun+1e-8]
        A2,b2,bd2,E2=with_abs(At,bt,bd);c2=np.zeros(nv+K);c2[nv:]=w;res=_solve(c2,A2,b2,bd2,E2,beq);tvv=float(s1.fun)
    else:
        A2,b2,bd2,E2=with_abs(A,b,bd);c2=np.zeros(nv+K);c2[2*K+1:2*K+1+nd]=1;c2[nv:]=prior_weight*w;res=_solve(c2,A2,b2,bd2,E2,beq);tvv=float(np.sum(res.x[2*K+1:2*K+1+nd])) if res.success else np.nan
    if not res.success:return LPResult(False,"tv solve failed",metadata={"n_intervals":K})
    x=res.x[:K];p=res.x[K:2*K];q=agg.costs_by_interval_user.T@x
    return LPResult(True,"ok",q,theta=float(res.x[2*K]),objective=float(res.fun),metadata={"n_intervals":K,"tv":tvv,"mean_inverse_rate":float(np.dot(C,x)/max(C.sum(),1e-12)),"estimated_total":float(p[-1]),"xhat":x})
