"""One-shot migration and journaled upstream group pricing ownership."""
from copy import deepcopy
from django.db import transaction
from django.utils import timezone

from ..history_state import LeaseBusyError, LeaseGuard
from ..integrations.sub2api import Sub2APIClient, Sub2APIError
from ..models import AppSettings, MonitoredAccount, UpstreamPricingState
from ..models.upstream_pricing import default_upstream_pricing_policy
from .planning import PricingPlanner, canonical_fields, managed_fields, normalize_policy


def _needs_revert(target):
    baseline = target.get("baseline")
    if baseline is None or target.get("status") == "reverted":
        return False
    return (
        canonical_fields(target.get("after")) != canonical_fields(baseline)
        or (
            target.get("write_pending", False)
            and canonical_fields(target.get("before")) != canonical_fields(baseline)
        )
    )


def _matches_journal(current, target):
    known = [target["baseline"], target["after"]]
    if target.get("write_pending"):
        known.append(target["before"])
    current = canonical_fields(current)
    return any(current == canonical_fields(value) for value in known)


def pricing_state_payload(state=None):
    state = state or UpstreamPricingState.load()
    return {
        "policy": state.policy, "status": state.status, "revision": state.revision,
        "selected_group_ids": state.selected_group_ids,
        "targets": [{key: row.get(key) for key in ("group_id", "group_name", "status", "error")} for row in state.targets],
        "attempted_at": state.attempted_at, "applied_at": state.applied_at,
        "reverted_at": state.reverted_at, "last_error": state.last_error,
        "announcement_applied_at": state.announcement_applied_at,
        "can_revert": any(_needs_revert(row) for row in state.targets),
    }


def _save(state, guard):
    with transaction.atomic():
        guard.assert_owned()
        state.save()


def _set_accounts(state, account_groups, errors, guard, *, previous=None, changed_ids=()):
    targets = {row["group_id"]: row for row in state.targets}
    managed = bool(state.policy["fast_rules"] or state.policy["model_rules"] or state.policy["long_context_pricing_enabled"] is not None)
    with transaction.atomic():
        guard.assert_owned()
        for account_id, groups in account_groups.items():
            applied = managed and bool(groups) and account_id not in errors and all(
                group_id in state.selected_group_ids and targets.get(group_id, {}).get("status") == "applied" for group_id in groups
            )
            epoch = f"upstream-v1:{state.revision}:{'applied' if applied else 'none'}"
            if applied and previous and account_id not in changed_ids:
                prior = previous.get(account_id)
                if prior and prior[0]:
                    epoch = prior[1]
            MonitoredAccount.objects.filter(pk=account_id).update(
                upstream_pricing_applied=applied,
                pricing_epoch=epoch,
            )


def apply_policy(policy, *, group_ids=None, automatic=False, announcement=False):
    guard = LeaseGuard.acquire(0)
    try:
        config = AppSettings.load()
        state = UpstreamPricingState.load()
        if announcement:
            if state.announcement_applied_at is not None:
                raise ValueError("公告一键应用已确认过，请在设置页调整或重试")
            policy = default_upstream_pricing_policy()
        if automatic and (state.attempted_at is not None or state.status != "pending"):
            return None
        group_ids = state.selected_group_ids if automatic else group_ids
        if not isinstance(group_ids, list) or not group_ids or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in group_ids
        ):
            raise ValueError("请至少选择一个有效的目标分组")
        group_ids = sorted(set(group_ids))
        try:
            policy = normalize_policy(policy)
        except ValueError as exc:
            if not automatic:
                raise
            state.attempted_at = timezone.now()
            state.status = "failed"
            state.last_error = str(exc)
            _save(state, guard)
            return state
        base_url = config.sub2api_base_url.rstrip("/")
        if state.base_url and state.base_url != base_url:
            if any(_needs_revert(row) for row in state.targets):
                raise ValueError("上游地址与计费日志不一致，请切回原服务并撤回原配置后再更换连接")
            state.targets = []
        accounts = list(MonitoredAccount.objects.filter(enabled=True, provider="sub2api"))
        previous = {account.pk: (account.upstream_pricing_applied, account.pricing_epoch) for account in accounts}
        changed_ids = set()
        if not config.sub2api_admin_token_encrypted:
            raise ValueError("请先配置 Sub2API 连接")
        state.policy = policy
        state.selected_group_ids = group_ids
        state.revision += 1
        state.base_url = base_url
        state.attempted_at = timezone.now()
        if announcement:
            state.announcement_applied_at = state.attempted_at
        state.status = "failed"
        state.last_error = "上游配置操作尚未完成；如进程中断，请检查日志后重试或撤回。"
        _save(state, guard)
        account_groups = {account.pk: [] for account in accounts}
        errors = {}
        # No observations during the global lease; interrupted writes remain unconfirmed.
        _set_accounts(state, account_groups, errors, guard)
        try:
            with Sub2APIClient(config) as client:
                planner = PricingPlanner(client, guard)
                owners = {group_id: [] for group_id in group_ids}
                for account in accounts:
                    try:
                        guard.renew()
                        account_groups[account.pk] = client.account_group_ids(account.external_account_id)
                        for group_id in account_groups[account.pk]:
                            if group_id in owners:
                                owners[group_id].append(account.pk)
                    except (Sub2APIError, ValueError) as exc:
                        errors[account.pk] = f"{account.name}：{exc}"
                by_group = {row["group_id"]: row for row in state.targets}
                for group_id, owner_ids in owners.items():
                    target = by_group.get(group_id)
                    if target is None:
                        target = {"group_id": group_id, "group_name": str(group_id), "status": "pending", "error": ""}
                        state.targets.append(target)
                    target["account_ids"] = sorted(set(target.get("account_ids", []) + owner_ids))
                    try:
                        guard.renew()
                        group = client.group_pricing(group_id)
                        if group.get("platform") != "openai":
                            raise ValueError("只能选择 OpenAI 分组")
                        target["group_name"] = group.get("name") or str(group_id)
                        current = managed_fields(group)
                        if target.get("status") == "reverted":
                            for key in ("baseline", "before", "after", "write_pending"):
                                target.pop(key, None)
                        if target.get("after") is not None and not _matches_journal(current, target):
                            raise ValueError("上游计费字段已被其他操作修改，拒绝覆盖")
                        baseline = target.get("baseline", current)
                        desired = planner.plan(group_id, baseline, policy)
                        needs_write = canonical_fields(current) != canonical_fields(desired)
                        target.update(baseline=deepcopy(baseline), before=current, after=desired, status="pending", error="", write_pending=needs_write)
                        _save(state, guard)
                        if needs_write:
                            changed_ids.update(target["account_ids"])
                            MonitoredAccount.objects.filter(pk__in=target["account_ids"]).update(
                                upstream_pricing_applied=False,
                                pricing_epoch=f"upstream-v1:{state.revision}:none",
                            )
                            guard.renew()
                            client.update_group_pricing(group_id, desired)
                        actual = managed_fields(client.group_pricing(group_id))
                        if canonical_fields(actual) != canonical_fields(desired):
                            raise ValueError("上游读回与目标计费配置不一致")
                        target.update(after=actual, status="applied", write_pending=False)
                    except (Sub2APIError, ValueError, KeyError) as exc:
                        target.update(status="failed", error=str(exc))
                    _save(state, guard)
                selected = [by for by in state.targets if by["group_id"] in owners]
                failures = [row for row in selected if row["status"] != "applied"]
                successes = [row for row in selected if row["status"] == "applied"]
                state.status = "applied" if successes and not failures and not errors else "partial" if successes else "failed"
                state.last_error = "；".join([*errors.values(), *(f'{row["group_name"]}：{row["error"]}' for row in failures)])
                if state.status == "applied":
                    state.applied_at = timezone.now()
        except (Sub2APIError, ValueError) as exc:
            state.status = "failed"
            state.last_error = str(exc)
            errors.update({account.pk: str(exc) for account in accounts})
        _set_accounts(state, account_groups, errors, guard, previous=previous, changed_ids=changed_ids)
        _save(state, guard)
        return state
    finally:
        guard.release()


def revert_policy(revision):
    guard = LeaseGuard.acquire(0)
    try:
        state = UpstreamPricingState.load()
        config = AppSettings.load()
        if state.revision != revision:
            raise ValueError("计费配置版本已变化，请刷新后撤回")
        if config.sub2api_base_url.rstrip("/") != state.base_url:
            raise ValueError("当前连接不是原计费配置所在服务，拒绝撤回")
        targets = [row for row in state.targets if _needs_revert(row)]
        if not targets:
            raise ValueError("没有可撤回的上游配置")
        state.revision += 1
        state.status = "failed"
        state.last_error = "撤回尚未完成；进程中断后可再次撤回。"
        _save(state, guard)
        owner_ids = {account for row in targets for account in row.get("account_ids", [])}
        _set_accounts(state, {account: [] for account in owner_ids}, {}, guard)
        with Sub2APIClient(config) as client:
            for target in targets:
                try:
                    guard.renew()
                    current = managed_fields(client.group_pricing(target["group_id"]))
                    if canonical_fields(current) == canonical_fields(target["baseline"]):
                        target.update(status="reverted", error="", write_pending=False)
                    else:
                        if not _matches_journal(current, target):
                            raise ValueError("上游配置已被外部修改，拒绝覆盖；请人工核对")
                        target.update(before=current, write_pending=True, status="pending")
                        _save(state, guard)
                        client.update_group_pricing(target["group_id"], target["baseline"])
                        actual = managed_fields(client.group_pricing(target["group_id"]))
                        if canonical_fields(actual) != canonical_fields(target["baseline"]):
                            raise ValueError("撤回后的上游读回结果不一致")
                        target.update(status="reverted", error="", write_pending=False)
                except (Sub2APIError, ValueError, KeyError) as exc:
                    target.update(status="failed", error=str(exc))
                _save(state, guard)
        failures = [row for row in targets if row["status"] != "reverted"]
        state.status = "partial" if failures and len(failures) < len(targets) else "failed" if failures else "reverted"
        state.last_error = "；".join(f'{row["group_name"]}：{row["error"]}' for row in failures)
        if not failures:
            state.reverted_at = timezone.now()
        _save(state, guard)
        return state
    finally:
        guard.release()


def run_automatic_migration():
    state = UpstreamPricingState.load()
    config = AppSettings.load()
    if state.attempted_at is not None or state.status != "pending" or not config.monitoring_enabled:
        return None
    if not state.selected_group_ids or not config.sub2api_admin_token_encrypted:
        return None
    try:
        return apply_policy(state.policy, automatic=True)
    except LeaseBusyError:
        return None
