"""Display the same persisted posterior used by particle trajectory replay.

Collection completeness concerns settlement, not the existence of a capacity
posterior. A model prior is explicitly labelled and never presented as measured.
"""

from decimal import Decimal


def particle_capacity_estimate(observation):
    if (
        observation is None
        or observation.attribution_started_at is None
        or not observation.model_diagnostics.get("algorithm")
        or observation.capacity_lower_usd is None
        or observation.capacity_upper_usd is None
        or observation.effective_usd_per_percent <= 0
    ):
        return None
    return {
        "source": "particle_filter",
        "capacity_usd": float(
            (observation.effective_usd_per_percent * 100).quantize(Decimal("0.01"))
        ),
        "lower_usd": float(observation.capacity_lower_usd.quantize(Decimal("0.01"))),
        "upper_usd": float(observation.capacity_upper_usd.quantize(Decimal("0.01"))),
        "prior_only": not observation.valid_sample,
        "as_of": observation.observed_at.isoformat(),
    }
