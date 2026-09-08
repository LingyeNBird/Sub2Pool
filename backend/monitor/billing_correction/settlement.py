"""Convert corrected model dollars into Sub2API wallet dollars."""

from decimal import Decimal

from ..fast_correction.prefix import FastCorrectionPrefix
from ..models import AppSettings, ParticipantSnapshot

ZERO = Decimal("0")
ONE = Decimal("1")


def balance_conversion_factor(
    snapshot: ParticipantSnapshot,
    config: AppSettings,
    *,
    correction_prefix: FastCorrectionPrefix | None = None,
    selected_cost: Decimal | None = None,
) -> Decimal:
    """Return wallet USD / corrected-model USD for one attribution interval.

    Complete request facts provide the exact ratio under the current rules.  A
    participant without such requests borrows the account's contemporaneous
    ratio.  Legacy aggregate data is used only after those samples are
    exhausted, and is always aligned to the participant's attribution start.
    """
    observation = snapshot.observation
    if observation.account_id < 0:
        return ONE
    started_at = observation.attribution_started_at
    if started_at is None:
        return ONE

    prefix = correction_prefix or FastCorrectionPrefix(
        observation.account_id,
        config.cost_basis,
        config,
    )
    user_id = snapshot.source_sub2api_user_id

    if user_id is not None:
        actual, corrected = prefix.user_sample_between(
            user_id, started_at, observation
        )
        if corrected > ZERO:
            return actual / corrected

    actual, corrected = prefix.sample_between(started_at, observation)
    if corrected > ZERO:
        return actual / corrected

    current_selected = Decimal(
        str(snapshot.selected_cost if selected_cost is None else selected_cost)
    )
    if user_id is not None and current_selected > ZERO:
        correction = prefix.user_between(
            user_id,
            started_at,
            observation.observed_at,
            observation_id=observation.id,
        )
        if config.cost_basis == "actual":
            raw_actual = current_selected - correction
            return raw_actual / current_selected

        paired = prefix.user_raw_between(user_id, started_at, observation)
        if paired is not None:
            actual, standard = paired
            if actual != ZERO or standard != ZERO:
                return actual / current_selected

    actual, standard = prefix.account_raw_between(started_at, observation)
    raw_selected = actual if config.cost_basis == "actual" else standard
    corrected = raw_selected + prefix.total_between(started_at, observation)
    if corrected > ZERO:
        return actual / corrected
    return ONE
