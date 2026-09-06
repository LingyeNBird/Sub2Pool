"""Opt-in sidecar capture. Costs remain upstream facts, never corrected outputs."""
from decimal import Decimal, InvalidOperation
from ..models.research import ResearchRequestComponents, ResearchSettings
from .protocol import STUDY, consent_digest


def capture_components(capture, logs):
    settings = ResearchSettings.objects.filter(pk=1, enabled=True).first()
    if settings is None or STUDY not in settings.projects or settings.consent_hash != consent_digest(settings.endpoint, settings.projects, settings.gateway_only):
        return
    rows = []
    facts = {fact.source_log_id: fact.pk for fact in capture.facts.all()}
    for log in logs:
        values = getattr(log, "component_costs", None)
        if values is None or len(values) != 4:
            continue
        try:
            values = [Decimal(str(value)) for value in values]
        except (ValueError, TypeError, InvalidOperation):
            continue
        if any(not value.is_finite() or not 0 <= value <= Decimal("1e12") or len(str(value)) > 96 for value in values):
            continue
        rows.append(ResearchRequestComponents(
            fact_id=facts[log.id], **dict(zip(
                ("input_cost", "cache_creation_cost", "cache_read_cost", "output_cost"), map(str, values), strict=True,
            )),
        ))
    ResearchRequestComponents.objects.bulk_create(rows, batch_size=500)
