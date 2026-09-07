"""Explicit opt-in, durable batches and central joint evidence; no quota estimates."""
import hashlib
import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from ..models.research import ResearchSettings, ResearchEvidenceBatch
from .pooled_data import collect_batches
from .pooled_protocol import STUDY, canonical, consent_digest, method_digest
from . import transport


def authorized(config):
    return config.enabled and STUDY in config.projects and config.consent_hash == consent_digest(config.endpoint, config.projects, config.gateway_only)


def _overview(batches):
    keys = ("requests", "gpt6_requests", "other_requests", "raw_usd", "gpt6_raw_usd", "quota_points", "intervals", "groups", "contrasts")
    result = {key: sum(b.summary.get(key, 0) for b in batches) for key in keys}
    result["batches"] = len(batches)
    result["archived_batches"] = sum(b.archived_source for b in batches)
    result["quality"] = {}
    for batch in batches:
        for key, count in batch.summary.get("quality", {}).items():
            result["quality"][key] = result["quality"].get(key, 0) + count
    result["preview"] = next((b.summary for b in reversed(batches) if b.summary), None)
    return result


def run_due(now=None):
    now = now or timezone.now()
    with transaction.atomic():
        config = ResearchSettings.objects.select_for_update().get(pk=ResearchSettings.load().pk)
        if not authorized(config):
            return "disabled"
        if config.next_run_at is not None and config.next_run_at > now:
            return "not_due"
        if config.lease_until is not None and config.lease_until > now:
            return "busy"
        token = str(uuid.uuid4())
        config.lease_token, config.lease_until = token, now + timedelta(minutes=15)
        config.last_status, config.last_error = "analyzing", ""
        config.save()
        version = config.config_revision
    outcome, failure, pending, sent = "analyzed", "", 0, 0
    try:
        batches = collect_batches(now, gateway_only=config.gateway_only)
        with transaction.atomic():
            current = ResearchSettings.objects.select_for_update().get(pk=1)
            if current.lease_token != token or current.config_revision != version or not authorized(current):
                return "consent_changed"
            current.summary, current.last_computed_at = _overview(batches), now
            current.save(update_fields=["summary", "last_computed_at"])
        if not transport.destination_ready(current.endpoint):
            outcome = "destination_unconfigured"
        elif not batches:
            outcome = "no_data"
        else:
            for batch in batches:
                if not batch.summary:
                    continue
                with transaction.atomic():
                    current = ResearchSettings.objects.select_for_update().get(pk=1)
                    if current.lease_token != token or current.config_revision != version or not authorized(current):
                        return "consent_changed"
                    _, public = transport.identity(current, current.endpoint)
                    receipt = hashlib.sha256(canonical([current.endpoint, public, method_digest()])).hexdigest()
                    digest = hashlib.sha256(canonical(batch.summary)).hexdigest()
                    if batch.sent_hashes.get(receipt) == digest:
                        continue
                    if sent >= 20:
                        pending += 1
                        continue
                    current.report_revision += 1
                    payload = transport.packet(current, batch.summary, batch_id=batch.pk)
                    current.last_sent_endpoint = current.endpoint
                    current.lease_until = timezone.now() + timedelta(minutes=15)
                    current.save()
                if not ResearchSettings.objects.filter(pk=1, enabled=True, config_revision=version, lease_token=token).exists():
                    return "consent_changed"
                ack = transport.send(current.endpoint, *payload)
                if type(ack.get("revision")) is not int or ack["revision"] != current.report_revision:
                    raise transport.DeliveryError("接收服务确认的版本不一致，统计未标记为已发送")
                with transaction.atomic():
                    current = ResearchSettings.objects.select_for_update().get(pk=1)
                    if current.config_revision != version or current.lease_token != token:
                        return "consent_changed"
                    saved = ResearchEvidenceBatch.objects.select_for_update().get(pk=batch.pk)
                    saved.sent_hashes[receipt] = digest
                    saved.save(update_fields=["sent_hashes"])
                    current.last_sent_at, current.last_sent_hash = timezone.now(), digest
                    current.save(update_fields=["last_sent_at", "last_sent_hash"])
                sent += 1
            outcome = "sent" if sent else "unchanged"
    except transport.DeliveryError as exc:
        outcome, failure = "delivery_failed", str(exc)
    except Exception:
        outcome, failure = "analysis_failed", "科研分析未完成，本地原始事实和已提交贡献未删除；稍后重试"
    finally:
        with transaction.atomic():
            current = ResearchSettings.objects.select_for_update().get(pk=1)
            if current.lease_token == token and current.config_revision == version:
                current.failures = current.failures + 1 if failure else 0
                hours = min(current.interval_hours, (5 * 2**min(current.failures, 6))/60) if failure else (1/60 if pending else current.interval_hours)
                current.next_run_at = timezone.now() + timedelta(hours=hours)
                current.last_status, current.last_error = outcome, failure[:160]
                current.lease_token, current.lease_until = "", None
                current.save()
    return outcome
