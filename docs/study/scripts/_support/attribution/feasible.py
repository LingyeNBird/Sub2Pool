"""Sparse optimization methods for attribution and identification.

Revision 2 changes the set-membership formulation in two ways:

1. It works on unique event-time groups, so events with identical timestamps share
   the same inverse conversion rate exactly.
2. The feasible minimax point is selected by an explicit hierarchy rather than by
   whichever optimal LP vertex the solver happens to return.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

from models import CycleData, aggregate_cycle
from simulate import X_MIN, X_MAX

EPS = 1e-8
CENTER_TOL = 1e-6
TV_FACE_TOL = 1e-8
TV_FACE_RETRY_TOL = 1e-7


@dataclass
class LPResult:
    success: bool
    message: str
    estimate: np.ndarray | None = None
    lower: np.ndarray | None = None
    upper: np.ndarray | None = None
    radius: float | None = None
    theta: float | None = None
    total_interval: tuple[float, float] | None = None
    objective: float | None = None
    metadata: dict | None = None


@dataclass
class EventSetProblem:
    """Linear feasible-set representation using one x variable per unique event time."""

    costs_group_user: np.ndarray
    group_times: np.ndarray
    interval_end_times: np.ndarray
    observed_z: np.ndarray
    A_ub: sparse.csr_matrix
    b_ub: np.ndarray
    A_eq: sparse.csr_matrix
    b_eq: np.ndarray
    bounds: list[tuple[float | None, float | None]]
    q_coeff: np.ndarray
    total_coeff: np.ndarray
    theta_idx: int
    n_base: int

    @property
    def n_users(self) -> int:
        return int(self.costs_group_user.shape[1])

    @property
    def n_groups(self) -> int:
        return int(self.costs_group_user.shape[0])

    @property
    def n_intervals(self) -> int:
        return int(len(self.interval_end_times))


def _solve(c, A, b, bounds, Aeq=None, beq=None):
    return linprog(
        c,
        A_ub=A,
        b_ub=b,
        A_eq=Aeq,
        b_eq=beq,
        bounds=bounds,
        method="highs",
        options={"presolve": True},
    )


def _D(K: int) -> sparse.csr_matrix:
    return sparse.diags(
        [np.ones(K), -np.ones(max(K - 1, 0))],
        [0, -1],
        shape=(K, K),
        format="csr",
    )


def _tb(theta):
    if theta is None:
        return (0.0, 1.0 - EPS)
    x = float(np.clip(theta, 0, 1 - EPS))
    return (x, x)


def _qrows(selector, z, tail=0, slack=0.0, cap=100):
    """Quantization strip rows for z <= selected_state + theta < z+1."""
    K = len(z)
    ap = sparse.hstack(
        [
            selector,
            sparse.csr_matrix(np.ones((K, 1))),
            sparse.csr_matrix((K, tail)),
        ],
        format="csr",
    )
    rows = [-ap]
    rhs = [-(z.astype(float) - slack)]
    mask = z < cap
    if np.any(mask):
        rows.append(ap[mask])
        rhs.append(z[mask].astype(float) + 1 + slack - EPS)
    return rows, rhs


def _group_events(cycle: CycleData) -> tuple[np.ndarray, np.ndarray]:
    """Return unique event times and group-by-user costs.

    Exact float equality defines simultaneity, matching the ideal model in which
    timestamps are exact. Events at the same time share one x(t) variable.
    """
    if len(cycle.event_times) == 0:
        return np.array([], float), np.zeros((0, cycle.n_users), float)
    times, inv = np.unique(cycle.event_times.astype(float), return_inverse=True)
    costs = np.zeros((len(times), cycle.n_users), float)
    np.add.at(costs, (inv, cycle.event_users), cycle.event_costs)
    return times, costs


def _event_set_problem(
    cycle: CycleData,
    known_theta=None,
    obs_slack: float = 0.0,
) -> EventSetProblem:
    """Build the exact weak-assumption feasible set.

    One inverse-rate variable is used for each unique event time. Hence the model
    is exact both when event times are all distinct and when multiple participants
    have simultaneous events. Between distinct event times x(t) may vary
    arbitrarily within [X_MIN, X_MAX].
    """
    agg = aggregate_cycle(cycle)
    group_times, Cgu = _group_events(cycle)
    G = len(group_times)
    K = len(agg.interval_end_times)
    n = cycle.n_users
    if K == 0:
        # This only occurs for a malformed cycle with no post-origin sample.
        raise ValueError("cycle contains no usable sampling interval")

    # Variables: x_1..x_G, cumulative p_1..p_K, theta.
    theta_idx = G + K
    nv = theta_idx + 1
    Aeq = sparse.lil_matrix((K, nv), dtype=float)
    beq = np.zeros(K, float)

    if G:
        interval_idx = np.searchsorted(agg.interval_end_times, group_times, side="left")
        interval_idx = np.clip(interval_idx, 0, K - 1)
        group_total = Cgu.sum(axis=1)
    else:
        interval_idx = np.array([], int)
        group_total = np.array([], float)

    for k in range(K):
        pidx = G + k
        Aeq[k, pidx] = 1.0
        if k:
            Aeq[k, G + k - 1] = -1.0
        if G:
            g = np.flatnonzero(interval_idx == k)
            if len(g):
                Aeq[k, g] = -group_total[g]

    # Quantization strips on cumulative p_k + theta.
    selector = sparse.hstack(
        [sparse.csr_matrix((K, G)), sparse.eye(K, format="csr")],
        format="csr",
    )
    qrows, qrhs = _qrows(selector, agg.observed_z, slack=obs_slack)
    A_ub = sparse.vstack(qrows, format="csr")
    b_ub = np.concatenate(qrhs)

    total_upper = float(cycle.event_costs.sum() * X_MAX)
    bounds: list[tuple[float | None, float | None]] = (
        [(X_MIN, X_MAX)] * G
        + [(0.0, total_upper)] * K
        + [_tb(known_theta)]
    )

    q_coeff = np.zeros((n, nv), float)
    if G:
        q_coeff[:, :G] = Cgu.T
    total_coeff = np.zeros(nv, float)
    total_coeff[G + K - 1] = 1.0

    return EventSetProblem(
        costs_group_user=Cgu,
        group_times=group_times,
        interval_end_times=agg.interval_end_times,
        observed_z=agg.observed_z,
        A_ub=A_ub,
        b_ub=b_ub,
        A_eq=Aeq.tocsr(),
        b_eq=beq,
        bounds=bounds,
        q_coeff=q_coeff,
        total_coeff=total_coeff,
        theta_idx=theta_idx,
        n_base=nv,
    )


def _extend_matrix(A: sparse.csr_matrix, extra_cols: int) -> sparse.csr_matrix:
    return sparse.hstack(
        [A, sparse.csr_matrix((A.shape[0], extra_cols))], format="csr"
    )


def _append_ub(A, b, row, rhs):
    row = sparse.csr_matrix(np.asarray(row, float).reshape(1, -1))
    return sparse.vstack([A, row], format="csr"), np.r_[b, float(rhs)]


def _append_eq(Aeq, beq, row, rhs):
    row = sparse.csr_matrix(np.asarray(row, float).reshape(1, -1))
    return sparse.vstack([Aeq, row], format="csr"), np.r_[beq, float(rhs)]


def _project_box_sum(v, z, lower, upper):
    """Euclidean projection onto {x: lower<=x<=upper, sum x=z}."""
    v = np.asarray(v, float)
    lower = np.asarray(lower, float)
    upper = np.asarray(upper, float)
    z = float(np.clip(z, lower.sum(), upper.sum()))
    if len(v) == 0:
        return v.copy()
    lo = float(np.min(v - upper) - 1.0)
    hi = float(np.max(v - lower) + 1.0)
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        x = np.clip(v - mid, lower, upper)
        if x.sum() > z:
            lo = mid
        else:
            hi = mid
    x = np.clip(v - 0.5 * (lo + hi), lower, upper)
    # One tiny residual correction on free coordinates.
    rem = z - x.sum()
    if abs(rem) > 1e-10:
        free = np.flatnonzero((x > lower + 1e-10) & (x < upper - 1e-10))
        if len(free):
            x[free] += rem / len(free)
    return np.clip(x, lower, upper)


def total_interval(cycle: CycleData, known_theta=None, obs_slack: float = 0.0):
    problem = _event_set_problem(cycle, known_theta, obs_slack)
    c = problem.total_coeff.copy()
    lo = _solve(
        c,
        problem.A_ub,
        problem.b_ub,
        problem.bounds,
        problem.A_eq,
        problem.b_eq,
    )
    hi = _solve(
        -c,
        problem.A_ub,
        problem.b_ub,
        problem.bounds,
        problem.A_eq,
        problem.b_eq,
    )
    return (float(lo.fun), float(-hi.fun)) if lo.success and hi.success else None


def _endpoint_intervals(problem: EventSetProblem):
    n = problem.n_users
    L = np.empty(n)
    U = np.empty(n)
    for i in range(n):
        c = problem.q_coeff[i]
        lo = _solve(
            c,
            problem.A_ub,
            problem.b_ub,
            problem.bounds,
            problem.A_eq,
            problem.b_eq,
        )
        hi = _solve(
            -c,
            problem.A_ub,
            problem.b_ub,
            problem.bounds,
            problem.A_eq,
            problem.b_eq,
        )
        if not lo.success or not hi.success:
            return None, None, f"endpoint infeasible user {i}"
        L[i] = lo.fun
        U[i] = -hi.fun
    return L, U, "ok"


def _primary_center(problem: EventSetProblem, L, U):
    """Return the primary feasible minimax LP and its solution."""
    n0 = problem.n_base
    n = problem.n_users
    rho_idx = n0
    A = _extend_matrix(problem.A_ub, 1)
    rows = sparse.lil_matrix((2 * n, n0 + 1), dtype=float)
    rhs = np.empty(2 * n)
    for i in range(n):
        rows[2 * i, :n0] = problem.q_coeff[i]
        rows[2 * i, rho_idx] = -1.0
        rhs[2 * i] = L[i]
        rows[2 * i + 1, :n0] = -problem.q_coeff[i]
        rows[2 * i + 1, rho_idx] = -1.0
        rhs[2 * i + 1] = -U[i]
    A = sparse.vstack([A, rows.tocsr()], format="csr")
    b = np.r_[problem.b_ub, rhs]
    Aeq = _extend_matrix(problem.A_eq, 1)
    bounds = problem.bounds + [(0.0, None)]
    c = np.zeros(n0 + 1)
    c[rho_idx] = 1.0
    res = _solve(c, A, b, bounds, Aeq, problem.b_eq)
    return res, A, b, bounds, Aeq, problem.b_eq, rho_idx


def _profile_signatures(problem: EventSetProblem) -> list[tuple]:
    """Observable participant signatures used only for permutation-equivariant ties."""
    C = problem.costs_group_user
    signatures: list[tuple] = []
    for i in range(problem.n_users):
        col = C[:, i]
        signatures.append(
            (tuple(col.tolist()), float(col.sum()), int(np.count_nonzero(col)))
        )
    return signatures


def _canonical_profile_order(
    problem: EventSetProblem, anchor: np.ndarray
) -> list[int]:
    """Label-free order from observable profiles and the equivariant anchor.

    If two users have exactly the same cost-time profile but different anchor
    coordinates, the anchor value breaks the tie.  If both profile and anchor are
    identical, the hierarchy first imposes equality of their attribution
    coordinates, so their within-group order is immaterial.
    """
    signatures = _profile_signatures(problem)
    return sorted(
        range(problem.n_users),
        key=lambda i: (signatures[i], float(anchor[i])),
    )


def _symmetric_profile_equalities(
    problem: EventSetProblem,
    anchor: np.ndarray,
    nv: int,
    tol: float,
) -> sparse.csr_matrix:
    """Equalize observationally indistinguishable users when the anchor agrees.

    This removes the otherwise unavoidable implicit array-index tie break for
    users whose entire observable cost-time profiles and anchor coordinates are
    identical.  Imposing the equalities does not change the primary optimum:
    the feasible set and the primary L-infinity objective are invariant under
    permutations within such a group, hence group averaging yields an equally
    optimal symmetric point.
    """
    signatures = _profile_signatures(problem)
    groups: dict[tuple, list[int]] = {}
    for i, sig in enumerate(signatures):
        key = (sig, round(float(anchor[i]) / max(tol, 1e-12)))
        groups.setdefault(key, []).append(i)
    rows: list[np.ndarray] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        ref = members[0]
        for i in members[1:]:
            row = np.zeros(nv)
            row[: problem.n_base] = problem.q_coeff[i] - problem.q_coeff[ref]
            rows.append(row)
    if not rows:
        return sparse.csr_matrix((0, nv))
    return sparse.csr_matrix(np.vstack(rows))


def _face_coordinate_ranges(problem, L, U, primary_radius, tol=CENTER_TOL):
    res, A, b, bounds, Aeq, beq, rho_idx = _primary_center(problem, L, U)
    if not res.success:
        return None
    row = np.zeros(len(bounds))
    row[rho_idx] = 1.0
    A, b = _append_ub(A, b, row, primary_radius + tol)
    lo = np.empty(problem.n_users)
    hi = np.empty(problem.n_users)
    for i in range(problem.n_users):
        c = np.r_[problem.q_coeff[i], 0.0]
        rlo = _solve(c, A, b, bounds, Aeq, beq)
        rhi = _solve(-c, A, b, bounds, Aeq, beq)
        if not rlo.success or not rhi.success:
            return None
        lo[i], hi[i] = rlo.fun, -rhi.fun
    return lo, hi


def minimax_face_audit(cycle: CycleData, known_theta=None, obs_slack=0.0):
    """Audit non-uniqueness of the primary feasible minimax solution set."""
    problem = _event_set_problem(cycle, known_theta, obs_slack)
    L, U, msg = _endpoint_intervals(problem)
    if L is None:
        return LPResult(False, msg)
    primary, *_ = _primary_center(problem, L, U)
    if not primary.success:
        return LPResult(False, "primary center infeasible", lower=L, upper=U)
    ranges = _face_coordinate_ranges(problem, L, U, float(primary.fun))
    if ranges is None:
        return LPResult(False, "face-range audit failed", lower=L, upper=U)
    lo, hi = ranges
    q = problem.q_coeff @ primary.x[: problem.n_base]
    return LPResult(
        True,
        "ok",
        q,
        L,
        U,
        float(primary.fun),
        float(primary.x[problem.theta_idx]),
        metadata={
            "face_lower": lo,
            "face_upper": hi,
            "face_diameter_linf": float(np.max(hi - lo)),
            "n_time_groups": problem.n_groups,
            "n_intervals": problem.n_intervals,
        },
    )


def _hierarchical_selector(
    problem: EventSetProblem,
    L: np.ndarray,
    U: np.ndarray,
    anchor: np.ndarray,
    canonicalize: bool = True,
    tol: float = CENTER_TOL,
):
    """Select one point from the primary minimax face by explicit hierarchy.

    Hierarchy:
      1. minimize primary minimax radius rho;
      2. minimize max_i |Q_i-anchor_i|;
      3. minimize sum_i |Q_i-anchor_i|;
      4. if needed, recursively fix coordinate midpoints in a canonical,
         label-free profile order.

    The fourth stage guarantees a deterministic participant vector even if the
    first three objectives still leave an optimal face.
    """
    primary, A0, b0, bounds0, Aeq0, beq0, rho_idx = _primary_center(problem, L, U)
    if not primary.success:
        return None, {"stage": "primary", "message": primary.message}

    n0 = problem.n_base
    n = problem.n_users
    dinf_idx = n0 + 1
    abs_start = n0 + 2
    nv = n0 + 2 + n

    # Extend primary constraints with d_inf and abs variables.
    A = _extend_matrix(A0, 1 + n)
    Aeq = _extend_matrix(Aeq0, 1 + n)
    b = b0.copy()
    beq = beq0.copy()
    bounds = bounds0 + [(0.0, None)] + [(0.0, None)] * n

    # Users with identical observable profiles and identical equivariant anchor
    # coordinates are mathematically exchangeable.  Equalizing them removes any
    # hidden dependence on their input-array order.
    sym_eq = _symmetric_profile_equalities(problem, anchor, nv, tol)
    if sym_eq.shape[0]:
        Aeq = sparse.vstack([Aeq, sym_eq], format="csr")
        beq = np.r_[beq, np.zeros(sym_eq.shape[0])]

    # Preserve primary optimum.
    row = np.zeros(nv)
    row[rho_idx] = 1.0
    A, b = _append_ub(A, b, row, float(primary.fun) + tol)

    # L-infinity and L1 distance to the anchor.
    rows = sparse.lil_matrix((4 * n, nv), dtype=float)
    rhs = np.empty(4 * n)
    for i in range(n):
        q = problem.q_coeff[i]
        rows[4 * i, :n0] = q
        rows[4 * i, dinf_idx] = -1.0
        rhs[4 * i] = anchor[i]
        rows[4 * i + 1, :n0] = -q
        rows[4 * i + 1, dinf_idx] = -1.0
        rhs[4 * i + 1] = -anchor[i]
        rows[4 * i + 2, :n0] = q
        rows[4 * i + 2, abs_start + i] = -1.0
        rhs[4 * i + 2] = anchor[i]
        rows[4 * i + 3, :n0] = -q
        rows[4 * i + 3, abs_start + i] = -1.0
        rhs[4 * i + 3] = -anchor[i]
    A = sparse.vstack([A, rows.tocsr()], format="csr")
    b = np.r_[b, rhs]

    c_inf = np.zeros(nv)
    c_inf[dinf_idx] = 1.0
    r_inf = _solve(c_inf, A, b, bounds, Aeq, beq)
    if not r_inf.success:
        return None, {"stage": "anchor_linf", "message": r_inf.message}
    row = np.zeros(nv)
    row[dinf_idx] = 1.0
    A, b = _append_ub(A, b, row, float(r_inf.fun) + tol)

    c_l1 = np.zeros(nv)
    c_l1[abs_start:] = 1.0
    r_l1 = _solve(c_l1, A, b, bounds, Aeq, beq)
    if not r_l1.success:
        return None, {"stage": "anchor_l1", "message": r_l1.message}
    row = np.zeros(nv)
    row[abs_start:] = 1.0
    A, b = _append_ub(A, b, row, float(r_l1.fun) + tol)

    # If the anchor itself is feasible on the primary face, Q=anchor is already
    # unique and no further LPs are needed.
    current = r_l1
    residual_widths = np.zeros(n)
    anchor_exact = float(r_inf.fun) <= 10 * tol and float(r_l1.fun) <= 10 * tol

    # Canonical recursive midpoint tie-break. Convexity guarantees every midpoint
    # of a coordinate projection remains feasible after earlier coordinates are fixed.
    if canonicalize and not anchor_exact:
        for i in _canonical_profile_order(problem, anchor):
            c = np.zeros(nv)
            c[:n0] = problem.q_coeff[i]
            rlo = _solve(c, A, b, bounds, Aeq, beq)
            rhi = _solve(-c, A, b, bounds, Aeq, beq)
            if not rlo.success or not rhi.success:
                return None, {"stage": "canonical_range", "message": f"user {i}"}
            lo = float(rlo.fun)
            hi = float(-rhi.fun)
            residual_widths[i] = hi - lo
            target = 0.5 * (lo + hi)
            row = np.zeros(nv)
            row[:n0] = problem.q_coeff[i]
            # The mathematical selector fixes Q_i exactly. Numerically we use a
            # symmetric tolerance band to avoid false infeasibility from accumulated
            # LP feasibility tolerances.
            fix_tol = max(10.0 * tol, 1e-7)
            A, b = _append_ub(A, b, row, target + fix_tol)
            A, b = _append_ub(A, b, -row, -target + fix_tol)
        current = _solve(np.zeros(nv), A, b, bounds, Aeq, beq)
        if not current.success:
            return None, {"stage": "canonical_final", "message": current.message}

    q = problem.q_coeff @ current.x[:n0]
    return current, {
        "q": q,
        "rho": float(primary.fun),
        "anchor_linf": float(r_inf.fun),
        "anchor_l1": float(r_l1.fun),
        "residual_face_width_before_fix_max": float(np.max(residual_widths)),
        "theta": float(current.x[problem.theta_idx]),
        "selection_rule": "rho->anchor_linf->anchor_l1->canonical_recursive_midpoint",
        "anchor_exactly_feasible": bool(anchor_exact),
    }


def identification_region(
    cycle: CycleData,
    known_theta=None,
    obs_slack: float = 0.0,
    exact_feasible_center: bool | None = None,
    selector: str | None = None,
    anchor: np.ndarray | None = None,
    canonicalize: bool = True,
):
    """Compute participant identification intervals and a named point selector.

    selector values:
      - ``box_midpoint``: bounded-simplex projection of coordinate midpoints;
      - ``primary_vertex``: diagnostic legacy behavior (arbitrary primary LP vertex);
      - ``midpoint_lex``: deterministic hierarchy anchored at coordinate midpoints;
      - ``anchor_lex``: same hierarchy using the supplied anchor vector.

    ``exact_feasible_center`` is retained for backward compatibility. True maps to
    ``midpoint_lex`` and False maps to ``box_midpoint``.
    """
    if selector is None:
        if exact_feasible_center is True:
            selector = "midpoint_lex"
        elif exact_feasible_center is False:
            selector = "box_midpoint"
        else:
            selector = "midpoint_lex"

    problem = _event_set_problem(cycle, known_theta, obs_slack)
    n = problem.n_users
    if problem.n_groups == 0:
        z = np.zeros(n)
        return LPResult(
            True,
            "empty",
            z,
            z,
            z,
            0.0,
            None,
            (0.0, 0.0),
            0.0,
            {"n_intervals": problem.n_intervals, "n_time_groups": 0},
        )

    L, U, msg = _endpoint_intervals(problem)
    if L is None:
        return LPResult(False, msg, metadata={"n_intervals": problem.n_intervals})

    ctot = problem.total_coeff
    lo = _solve(ctot, problem.A_ub, problem.b_ub, problem.bounds, problem.A_eq, problem.b_eq)
    hi = _solve(-ctot, problem.A_ub, problem.b_ub, problem.bounds, problem.A_eq, problem.b_eq)
    if not lo.success or not hi.success:
        return LPResult(False, "total infeasible", lower=L, upper=U)
    total_interval_value = (float(lo.fun), float(-hi.fun))
    total_center = 0.5 * sum(total_interval_value)
    midpoint = 0.5 * (L + U)
    unrestricted = float(np.max((U - L) / 2.0))

    base_meta = {
        "n_intervals": problem.n_intervals,
        "n_time_groups": problem.n_groups,
        "simultaneous_group_count": int(
            np.sum(np.count_nonzero(problem.costs_group_user, axis=1) > 1)
        ),
        "minimax_unrestricted_radius": unrestricted,
        "set_formulation": "unique_event_time_shared_rate",
    }

    if selector == "box_midpoint":
        q = _project_box_sum(midpoint, total_center, L, U)
        radius = float(np.max(np.maximum(q - L, U - q)))
        return LPResult(
            True,
            "box_midpoint",
            q,
            L,
            U,
            radius,
            None,
            total_interval_value,
            None,
            {
                **base_meta,
                "center_temporally_certified": False,
                "selection_rule": "bounded_simplex_projection_of_coordinate_midpoint",
            },
        )

    if selector == "primary_vertex":
        primary, *_ = _primary_center(problem, L, U)
        if not primary.success:
            return LPResult(False, "primary center infeasible", lower=L, upper=U)
        q = problem.q_coeff @ primary.x[: problem.n_base]
        return LPResult(
            True,
            "primary_vertex",
            q,
            L,
            U,
            float(primary.fun),
            float(primary.x[problem.theta_idx]),
            total_interval_value,
            float(primary.fun),
            {
                **base_meta,
                "center_temporally_certified": True,
                "selection_rule": "unspecified_solver_vertex_diagnostic_only",
            },
        )

    if selector == "midpoint_lex":
        anchor_vec = midpoint
    elif selector == "anchor_lex":
        if anchor is None:
            raise ValueError("anchor_lex requires an anchor vector")
        anchor_vec = np.asarray(anchor, float)
        if anchor_vec.shape != (n,):
            raise ValueError("anchor has wrong shape")
    else:
        raise ValueError(f"unknown selector {selector}")

    selected, smeta = _hierarchical_selector(
        problem, L, U, anchor_vec, canonicalize=canonicalize
    )
    if selected is None:
        return LPResult(False, smeta.get("message", "selector failed"), lower=L, upper=U)
    q = np.asarray(smeta.pop("q"), float)
    return LPResult(
        True,
        "ok",
        q,
        L,
        U,
        float(smeta["rho"]),
        float(smeta["theta"]),
        total_interval_value,
        float(smeta["rho"]),
        {
            **base_meta,
            **smeta,
            "center_temporally_certified": True,
            "anchor_type": "coordinate_midpoint" if selector == "midpoint_lex" else "external",
        },
    )


# ---------------------------------------------------------------------------
# Total-variation estimator (unchanged in statistical meaning from version 1)
# ---------------------------------------------------------------------------


def _tv_problem(agg, known_theta, obs_slack):
    C = agg.total_costs
    K = len(C)
    nd = max(K - 1, 0)
    nv = 2 * K + 1 + nd
    Aeq = sparse.hstack(
        [
            -sparse.diags(C, format="csr"),
            _D(K),
            sparse.csr_matrix((K, 1 + nd)),
        ],
        format="csr",
    )
    beq = np.zeros(K)
    sel = sparse.hstack(
        [sparse.csr_matrix((K, K)), sparse.eye(K, format="csr")], format="csr"
    )
    rows, rhs = _qrows(sel, agg.observed_z, tail=nd, slack=obs_slack)
    if nd:
        tv = sparse.lil_matrix((2 * nd, nv))
        for k in range(nd):
            dc = 2 * K + 1 + k
            tv[2 * k, k + 1] = 1
            tv[2 * k, k] = -1
            tv[2 * k, dc] = -1
            tv[2 * k + 1, k + 1] = -1
            tv[2 * k + 1, k] = 1
            tv[2 * k + 1, dc] = -1
        rows.append(tv.tocsr())
        rhs.append(np.zeros(2 * nd))
    A = sparse.vstack(rows, format="csr")
    b = np.concatenate(rhs)
    pup = float(C.sum() * X_MAX)
    bd = (
        [(X_MIN, X_MAX)] * K
        + [(0, pup)] * K
        + [_tb(known_theta)]
        + [(0, None)] * nd
    )
    return A, b, bd, Aeq, beq, K, nd, nv


def tv_rate_estimate(
    cycle,
    known_theta=None,
    obs_slack=0.0,
    prior=None,
    prior_weight=0.0,
):
    agg = aggregate_cycle(cycle)
    A, b, bd, Aeq, beq, K, nd, nv = _tv_problem(agg, known_theta, obs_slack)
    if K == 0:
        return LPResult(False, "no intervals")
    prior = float(
        np.clip(
            0.5 * (X_MIN + X_MAX) if prior is None else prior,
            X_MIN,
            X_MAX,
        )
    )
    C = agg.total_costs
    w = C / max(C.sum(), 1e-12)

    def with_abs(A0, b0, bd0):
        Ae = sparse.hstack(
            [A0, sparse.csr_matrix((A0.shape[0], K))], format="csr"
        )
        ar = sparse.lil_matrix((2 * K, nv + K))
        ab = np.empty(2 * K)
        for k in range(K):
            ac = nv + k
            ar[2 * k, k] = 1
            ar[2 * k, ac] = -1
            ab[2 * k] = prior
            ar[2 * k + 1, k] = -1
            ar[2 * k + 1, ac] = -1
            ab[2 * k + 1] = -prior
        return (
            sparse.vstack([Ae, ar.tocsr()], format="csr"),
            np.r_[b0, ab],
            bd0 + [(0, None)] * K,
            sparse.hstack([Aeq, sparse.csr_matrix((K, K))], format="csr"),
        )

    if prior_weight <= 0:
        c = np.zeros(nv)
        c[2 * K + 1 :] = 1
        s1 = _solve(c, A, b, bd, Aeq, beq)
        if not s1.success:
            return LPResult(False, "tv infeasible", metadata={"n_intervals": K})
        At, bt = A, b
        if nd:
            row = np.zeros(nv)
            row[2 * K + 1 :] = 1
            At = sparse.vstack([A, sparse.csr_matrix(row[None, :])])
            bt = np.r_[b, s1.fun + TV_FACE_TOL * max(1.0, abs(float(s1.fun)))]
        A2, b2, bd2, E2 = with_abs(At, bt, bd)
        c2 = np.zeros(nv + K)
        c2[nv:] = w
        res = _solve(c2, A2, b2, bd2, E2, beq)
        tv_face_retry = False
        tv_face_tolerance = TV_FACE_TOL
        if not res.success and nd:
            # HiGHS may report false infeasibility when the stage-one optimal face
            # is fixed closer than its own feasibility tolerance. Retry only the
            # exceptional case with a still-negligible 1e-7 objective band. All
            # ordinary successful runs retain the original 1e-8 definition.
            bt_retry = bt.copy()
            bt_retry[-1] = float(s1.fun) + TV_FACE_RETRY_TOL * max(
                1.0, abs(float(s1.fun))
            )
            A2, b2, bd2, E2 = with_abs(At, bt_retry, bd)
            res = _solve(c2, A2, b2, bd2, E2, beq)
            tv_face_retry = bool(res.success)
            tv_face_tolerance = TV_FACE_RETRY_TOL
        tvv = float(s1.fun)
    else:
        A2, b2, bd2, E2 = with_abs(A, b, bd)
        c2 = np.zeros(nv + K)
        c2[2 * K + 1 : 2 * K + 1 + nd] = 1
        c2[nv:] = prior_weight * w
        res = _solve(c2, A2, b2, bd2, E2, beq)
        tvv = (
            float(np.sum(res.x[2 * K + 1 : 2 * K + 1 + nd]))
            if res.success
            else np.nan
        )
        tv_face_retry = False
        tv_face_tolerance = np.nan
    if not res.success:
        return LPResult(False, "tv solve failed", metadata={"n_intervals": K})
    x = res.x[:K]
    p = res.x[K : 2 * K]
    q = agg.costs_by_interval_user.T @ x
    return LPResult(
        True,
        "ok",
        q,
        theta=float(res.x[2 * K]),
        objective=float(res.fun),
        metadata={
            "n_intervals": K,
            "tv": tvv,
            "tv_face_retry": bool(tv_face_retry),
            "tv_face_tolerance": float(tv_face_tolerance),
            "mean_inverse_rate": float(np.dot(C, x) / max(C.sum(), 1e-12)),
            "estimated_total": float(p[-1]),
            "xhat": x,
            "selection_rule": "minimum_tv_then_weighted_l1_to_prior",
        },
    )
