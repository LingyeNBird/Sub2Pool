"""Single-flight opt-in scheduler; analysis errors cannot stop quota monitoring."""
import hashlib
import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from ..models.research import ResearchSettings
from .data import collect_cycles
from .estimator import analyze
from .protocol import STUDY, MIN_REQUESTS, canonical, consent_digest
from . import transport


def authorized(config):
    return config.enabled and STUDY in config.projects and config.consent_hash == consent_digest(config.endpoint, config.projects, config.gateway_only)


def run_due(now=None):
    now = now or timezone.now()
    with transaction.atomic():
        config = ResearchSettings.load()
        if not authorized(config):
            return "disabled"
        if config.next_run_at is not None and config.next_run_at > now:
            return "not_due"
        if config.lease_until is not None and config.lease_until > now:
            return "busy"
        token = str(uuid.uuid4())
        config.lease_token = token
        config.lease_until = now + timedelta(minutes=15)
        config.last_status, config.last_error = "analyzing", ""
        config.save()
        version = config.config_revision
    outcome, failure = "analyzed", ""
    try:
        cycles, exclusions = collect_cycles(now)
        summary = analyze(cycles, exclusions, gateway_only=config.gateway_only)
        digest = hashlib.sha256(canonical(summary)).hexdigest()
        with transaction.atomic():
            current = ResearchSettings.objects.select_for_update().get(pk=1)
            if current.lease_token != token or current.config_revision != version or not authorized(current):
                return "consent_changed"
            current.summary, current.last_computed_at = summary, now
            current.last_status = "analyzed"
            can_send = transport.destination_ready(current.endpoint) and summary["requests"] >= MIN_REQUESTS
            unchanged = current.last_sent_hash == digest and current.last_sent_endpoint == current.endpoint
            payload = None
            if can_send and not unchanged:
                current.report_revision += 1
                payload = transport.packet(current, summary)
                # Remember uncertain deliveries too: a timeout may occur after
                # server commit, so withdrawal must remain available.
                current.last_sent_endpoint = current.endpoint
            elif not transport.destination_ready(current.endpoint):
                outcome = "destination_unconfigured"
            elif summary["requests"] < MIN_REQUESTS:
                outcome = "insufficient_data"
            elif unchanged:
                outcome = "unchanged"
            current.save()
        if payload:
            # This is the network admission boundary. A request admitted before
            # disabling may finish; the consent dialog explicitly explains this.
            admitted = ResearchSettings.objects.filter(pk=1, enabled=True, config_revision=version, lease_token=token).exists()
            if not admitted:
                return "consent_changed"
            ack = transport.send(current.endpoint, *payload)
            if ack.get("revision") != current.report_revision:
                raise transport.DeliveryError("接收服务确认的版本不一致，统计未标记为已发送")
            with transaction.atomic():
                ResearchSettings.objects.filter(pk=1, config_revision=version, lease_token=token).update(
                    last_sent_at=timezone.now(), last_sent_hash=digest, last_sent_endpoint=current.endpoint,
                )
            outcome = "sent"
    except transport.DeliveryError as exc:
        outcome, failure = "delivery_failed", str(exc)
    except Exception:
        outcome, failure = "analysis_failed", "科研分析未完成，原始事实和额度测算不受影响；稍后重试"
    finally:
        with transaction.atomic():
            current = ResearchSettings.objects.select_for_update().get(pk=1)
            if current.lease_token == token and current.config_revision == version:
                current.failures = current.failures + 1 if failure else 0
                hours = min(current.interval_hours, (5 * 2**min(current.failures, 6))/60) if failure else current.interval_hours
                current.next_run_at = timezone.now() + timedelta(hours=hours)
                current.last_status, current.last_error = outcome, failure
                current.lease_token, current.lease_until = "", None
                current.save()
    return outcome


def withdraw():
    """Explicitly authorized removal, including while future sharing is disabled."""
    signing_error = None
    with transaction.atomic():
        config = ResearchSettings.objects.select_for_update().get(pk=ResearchSettings.load().pk)
        config.enabled = False
        config.config_revision += 1
        config.next_run_at = None
        config.lease_token, config.lease_until = "", None
        if not config.last_sent_endpoint:
            config.last_status = "disabled"
            config.save()
            return "nothing_sent"
        config.report_revision += 1
        try:
            # A missing seed cannot withdraw an earlier identity. packet() is
            # allowed to create a seed for new reports, not for this operation.
            if not config.identity_encrypted:
                raise transport.DeliveryError(transport.IDENTITY_ERROR_MESSAGE)
            payload = transport.packet(config, endpoint=config.last_sent_endpoint, withdraw=True)
            config.last_status, config.last_error = "withdrawing", ""
        except transport.DeliveryError as exc:
            signing_error = exc
            config.last_status, config.last_error = "withdrawal_failed", str(exc)
        config.save()
    # Raise only AFTER committing the explicit stop. A bad key must not roll
    # back enabled=False or turn an admin withdrawal into an unhandled 500.
    if signing_error is not None:
        raise signing_error
    try:
        ack = transport.send(config.last_sent_endpoint, *payload)
        if ack.get("revision") != config.report_revision:
            raise transport.DeliveryError("撤回版本未获确认")
    except transport.DeliveryError:
        ResearchSettings.objects.filter(pk=1, config_revision=config.config_revision).update(last_status="withdrawal_failed", last_error="已停止后续发送，但撤回未成功；请再次点击撤回")
        raise
    ResearchSettings.objects.filter(pk=1, config_revision=config.config_revision).update(last_sent_hash="", last_sent_endpoint="", last_status="withdrawn", last_error="")
    return "withdrawn"
