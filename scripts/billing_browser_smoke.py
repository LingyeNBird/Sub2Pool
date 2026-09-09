"""Isolated browser regression. Never use a production database or credentials.

Run with backend's dev dependencies plus playwright==1.57.0 installed,
a Chromium installed by Playwright, and frontend dependencies installed:
    backend/.venv/bin/python scripts/billing_browser_smoke.py
Artifacts and the temporary SQLite database stay in BILLING_REVIEW_OUTPUT.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
from tempfile import mkdtemp
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ.get("BILLING_REVIEW_OUTPUT", ROOT / "billing-review-output")).resolve()
BACKEND_PORT = int(os.environ.get("BILLING_BACKEND_PORT", "8000"))
FRONTEND_PORT = int(os.environ.get("BILLING_FRONTEND_PORT", "5173"))
FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}"
# The fixture MUST live below the designated isolated output directory.
OUTPUT.mkdir(parents=True, exist_ok=True)
os.environ.update(
    DJANGO_SETTINGS_MODULE="pinche.settings", DJANGO_DEBUG="true",
    DJANGO_SECRET_KEY="synthetic-pricing-browser-isolated-secret",
    DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1,testserver",
    DJANGO_CSRF_TRUSTED_ORIGINS=FRONTEND_URL,
    PINCH_DATA_DIR=mkdtemp(prefix="database-", dir=OUTPUT),
    WEBRTC_IP_COLLECTION_ENABLED="false", COOKIE_SECURE="false",
    VITE_DEMO_MODE="false", VITE_API_TARGET=f"http://127.0.0.1:{BACKEND_PORT}",
)
sys.path.insert(0, str(ROOT / "backend"))
import django  # noqa: E402
django.setup()
from django.core.management import call_command  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402
from monitor.models import AppSettings, Observation, AnnouncementRead, ObservationBillingCapture, BillingUsageFact  # noqa: E402
from monitor.tests.helpers import create_monitored_account, create_participant, historical_pricing  # noqa: E402
from monitor.tests.billing_correction.test_corrections import log  # noqa: E402
from monitor.fast_correction.domain import aggregate_fast_logs  # noqa: E402
from monitor.fast_correction.persistence import apply_fast_interval  # noqa: E402
from monitor.fast_correction.rules import FastCorrectionRuleSet  # noqa: E402
from monitor.replay import rebuild_account  # noqa: E402
from monitor.secrets import encrypt_secret  # noqa: E402
from monitor.announcements import ANNOUNCEMENTS  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from decimal import Decimal as D  # noqa: E402
from playwright.sync_api import sync_playwright, expect  # noqa: E402
from billing_rule_layout import check_rule_layout  # noqa: E402
from monitor.upstream_pricing.service import apply_policy, run_automatic_migration  # noqa: E402
from monitor.models import UpstreamPricingState  # noqa: E402


UPSTREAM = {"rows": [], "calls": [], "fail_next": False}
SEED_IDS = {}
GROUP_BASELINE = {
    "id": 7, "name": "Synthetic pricing group", "platform": "openai",
    "model_pricing": [], "long_context_pricing_enabled": True,
    "free_openai_fast": False, "rate_multiplier": 0.7,
}
UPSTREAM["group"] = deepcopy(GROUP_BASELINE)
UPSTREAM["pricing_fail_next"] = False
UPSTREAM["other_group"] = {
    **deepcopy(GROUP_BASELINE), "id": 8, "name": "Synthetic unselected group",
    "model_pricing": [
        *[{"platform": "openai", "models": [f"inherit-{index:02}"]} for index in range(24)],
        {"platform": "openai", "models": ["modified-zero"], "input_price": 0, "fast_multiplier": 0},
        {"platform": "openai", "models": ["gpt-6-astra"],
         "input_price": 0.000005, "output_price": 0.00003, "fast_multiplier": 3},
    ],
}


class SyntheticUpstream(BaseHTTPRequestHandler):
    """Loopback-only synthetic pricing and usage APIs; never forwards requests."""
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        path = urlparse(self.path).path
        UPSTREAM["calls"].append({"path": path, "query": query, "method": "GET"})
        if self.headers.get("x-api-key") != "Synthetic-Sub2API-Local-Only":
            self.send_json(400, {"message": "Unexpected synthetic upstream request"})
            return
        if path == "/api/v1/admin/accounts":
            # The existing settings page automatically discovers accounts.
            self.send_json(200, {"code": 0, "data": {"items": [{
                "id": 7, "name": "Synthetic account", "platform": "openai",
                "type": "oauth", "status": "active", "schedulable": True,
            }], "page": 1, "pages": 1, "total": 1}})
            return
        data = None
        if path == "/api/v1/admin/groups/all":
            data = [{"id": 7, "name": "Synthetic pricing group", "platform": "openai"},
                    {"id": 8, "name": "Synthetic unselected group", "platform": "openai"}]
        if path == "/api/v1/admin/accounts/7":
            data = {"id": 7, "platform": "openai", "group_ids": [7]}
        elif path == "/api/v1/admin/groups/7":
            data = UPSTREAM["group"]
        elif path == "/api/v1/admin/groups/8":
            data = UPSTREAM["other_group"]
        elif path == "/api/v1/admin/channels":
            data = {"items": [], "pages": 1}
        elif path == "/api/v1/admin/channels/pricing/sync-models":
            data = {"models": ["gpt-6-astra", "gpt-5.6-codex"]}
        elif path == "/api/v1/admin/channels/model-pricing":
            data = {"found": True, "input_price": 0.000005, "output_price": 0.00003,
                    "cache_write_price": 0.00000625, "cache_write_1h_price": 0.00001,
                    "cache_read_price": 0.0000005}
        if data is not None:
            self.send_json(200, {"code": 0, "data": data})
            return
        if path != "/api/v1/admin/usage":
            self.send_json(400, {"message": "Unexpected synthetic upstream path"})
            return
        if UPSTREAM["fail_next"]:
            UPSTREAM["fail_next"] = False
            self.send_json(503, {"message": "Synthetic upstream outage"})
            return
        zone = ZoneInfo(query["timezone"][0])
        rows = [row for row in UPSTREAM["rows"]
                if query["start_date"][0] <= datetime.fromisoformat(row["created_at"]).astimezone(zone).date().isoformat() <= query["end_date"][0]]
        self.send_json(200, {"code": 0, "data": {
            "items": rows, "page": 1, "pages": 1,
            "total": len(rows), "page_size": int(query["page_size"][0]),
        }})

    def do_PUT(self):
        if self.path != "/api/v1/admin/groups/7" or self.headers.get("x-api-key") != "Synthetic-Sub2API-Local-Only":
            self.send_json(400, {"message": "Unexpected synthetic write"})
            return
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert set(payload) == {"model_pricing", "long_context_pricing_enabled", "free_openai_fast"}
        UPSTREAM["calls"].append({"path": self.path, "method": "PUT", "fields": sorted(payload)})
        if UPSTREAM["pricing_fail_next"]:
            UPSTREAM["pricing_fail_next"] = False
            self.send_json(503, {"message": "Synthetic pricing outage"})
            return
        UPSTREAM["group"].update(payload)
        self.send_json(200, {"code": 0, "data": UPSTREAM["group"]})

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass  # No headers or synthetic credentials in evidence logs.


def database_snapshot():
    # Playwright runs an event loop on its thread. Keep ORM checks on a separate
    # synchronous connection instead of disabling Django's async-safety guard.
    def read():
        from django.db import connections
        try:
            return {
                "captures": list(ObservationBillingCapture.objects.values_list("observation_id", flat=True)),
                "facts": BillingUsageFact.objects.count(),
                "observations": {row.id: {"cost": row.selected_total_cost, "fast": row.fast_correction_actual_cost}
                                 for row in Observation.objects.all()},
            }
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(read).result()


def seed(upstream_url):
    call_command("migrate", verbosity=0)
    user, _ = get_user_model().objects.get_or_create(username="billing-reviewer", defaults={"is_staff": True, "is_superuser": True})
    user.set_password("Synthetic-Local-Review-2026!")
    user.save()
    for announcement in ANNOUNCEMENTS:
        AnnouncementRead.objects.get_or_create(
            user=user, announcement_code=announcement.code,
            defaults={"read_at": timezone.now()},
        )
    config = AppSettings.load()
    config.monitoring_enabled = True
    config.auto_apply_recommendations = False
    config.sub2api_base_url = upstream_url
    config.sub2api_admin_token_encrypted = encrypt_secret("Synthetic-Sub2API-Local-Only")
    config.weekly_quota_model = "constant_average"
    config.save()
    create_monitored_account()
    create_participant(name="Synthetic reviewer", sub2api_user_id=51, share_percent=100)
    start = timezone.now().replace(microsecond=0) - timedelta(hours=6)
    for index in range(1, 7):
        at = start + timedelta(hours=index)
        observation = Observation.objects.create(
            **historical_pricing(config),
            account_id=7, observed_at=at, window_seconds=604800,
            upstream_resets_at=start + timedelta(days=7), attribution_started_at=start,
            upstream_used_percent=D(index * 5), interval_used_percent=D(index * 5),
            total_actual_cost=D(100 * index), total_standard_cost=D(200 * index),
            selected_total_cost=D(100 * index), raw_selected_total_cost=D(100 * index),
            effective_usd_per_percent=D(20), raw_window={"query_mode": "passive"},
        )
        interval = aggregate_fast_logs(
            [log(id=index, created_at=at-timedelta(seconds=1))],
            started_at=at-timedelta(hours=1), ended_at=at,
            rules=FastCorrectionRuleSet(config.fast_correction_rules),
        )
        apply_fast_interval(observation, interval)
        observation.save()
        SEED_IDS[index] = observation.id
        raw = interval.logs[0]
        UPSTREAM["rows"].append({
            "id": raw.id, "account_id": raw.account_id, "user_id": raw.user_id,
            "created_at": raw.created_at.isoformat(), "model": raw.model,
            "service_tier": raw.service_tier, "total_cost": str(raw.total_cost),
            "actual_cost": str(raw.actual_cost), "input_tokens": raw.input_tokens,
            "cache_creation_tokens": raw.cache_creation_tokens, "cache_read_tokens": raw.cache_read_tokens,
            "long_context_billing_applied": raw.long_context_billing_applied,
            "api_key_id": raw.api_key_id, "api_key": {"name": raw.api_key_name},
        })
        if index in (2, 3):
            # Real pre-upgrade states: old FAST subtotal and completely missing.
            ObservationBillingCapture.objects.filter(observation=observation).delete()
            if index == 3:
                observation.fast_corrections.all().delete()
                Observation.objects.filter(pk=observation.id).update(
                    fast_correction_started_at=None, fast_correction_actual_cost=None,
                    fast_correction_standard_cost=None, fast_correction_request_count=None,
                )
    rebuild_account(7, config)
    assert run_automatic_migration() is None
    migrated = apply_policy(UpstreamPricingState.load().policy, group_ids=[7])
    assert migrated.status == "applied"
    assert run_automatic_migration() is None
    account = create_monitored_account()
    newer = Observation.objects.create(
        account_id=7, observed_at=timezone.now(), window_seconds=604800,
        upstream_resets_at=start+timedelta(days=7), upstream_used_percent=35,
        total_actual_cost=700, total_standard_cost=1400,
        raw_selected_total_cost=700, selected_total_cost=700,
        effective_usd_per_percent=20, raw_window={"query_mode": "passive"},
        correction_source="upstream", pricing_epoch=account.pricing_epoch,
    )
    SEED_IDS["upstream"] = newer.pk
    rebuild_account(7, config)


def wait_for_server(url):
    for _ in range(120):
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return
        except Exception:
            time.sleep(.5)
    raise RuntimeError(f"Server did not start: {url}")


def smoke():
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("BILLING_BROWSER_EXECUTABLE") or None)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_default_timeout(15000)
        try:
            page.goto(FRONTEND_URL + "/login")
            page.get_by_label("用户名", exact=True).fill("billing-reviewer")
            page.get_by_label("密码", exact=True).fill("Synthetic-Local-Review-2026!")
            page.get_by_role("button", name="登录", exact=True).click()
            page.wait_for_url(FRONTEND_URL + "/")
            page.goto(FRONTEND_URL + "/settings")
            heading = page.get_by_role("heading", name="Sub2API 上游计费", exact=True)
            expect(heading).to_be_visible()
            card = page.locator("section.card").filter(has=heading)
            expect(card.get_by_role("checkbox")).to_have_count(0)
            for label in ("FAST 目标倍率", "模型倍率"):
                help_button = card.get_by_role("button", name=f"查看{label}说明", exact=True)
                expect(help_button).to_be_visible()
                help_button.focus()
                expect(help_button).to_be_focused()
                help_button.hover()
                page.wait_for_timeout(250)
                assert help_button.evaluate("(button) => getComputedStyle(button.parentElement, '::before').visibility") == "visible"
                page.screenshot(path=str(OUTPUT / f"help-{'fast' if label.startswith('FAST') else 'model'}.png"), full_page=True)
            writes_before = sum(call["method"] == "PUT" for call in UPSTREAM["calls"])
            model_multiplier = card.locator("section").filter(
                has=page.get_by_role("heading", name="模型倍率", exact=True)
            ).get_by_label("倍率", exact=True)
            model_multiplier.fill("5")
            for kind, title in (("fast", "FAST 倍率"), ("model", "模型倍率"), ("context", "长上下文阶梯计费")):
                card.get_by_role("button", name=f"查看当前{title}", exact=True).click()
                inspector = page.get_by_role("dialog", name=f"当前{title}", exact=True)
                expect(inspector.get_by_role("tab", name=re.compile("Synthetic pricing group"))).to_have_attribute("aria-selected", "true")
                if kind == "fast":
                    expect(inspector.get_by_text("2.5×", exact=True).first).to_be_visible()
                elif kind == "model":
                    expect(inspector.locator("summary").filter(has_text="gpt-6-astra")).to_contain_text("1.8×")
                    expect(inspector.get_by_text("分组免费 FAST", exact=False)).to_have_count(0)
                else:
                    expect(inspector.get_by_role("tabpanel").get_by_text("关闭", exact=True)).to_be_visible()
                inspector.get_by_role("tab", name=re.compile("Synthetic unselected group")).click()
                if kind == "fast":
                    expect(inspector.get_by_text("3×", exact=True)).to_be_visible()
                elif kind == "model":
                    expect(inspector.locator("summary").filter(has_text="gpt-6-astra")).to_contain_text("1×")
                else:
                    expect(inspector.get_by_role("tabpanel").get_by_text("启用", exact=True)).to_be_visible()
                if kind != "context":
                    rows = inspector.locator("tbody tr") if kind == "fast" else inspector.locator("summary")
                    expect(rows).to_have_count(10)
                    expect(inspector.get_by_text("共 26 条 · 第 1 / 3 页", exact=True)).to_be_visible()
                    expect(rows.first).to_contain_text("modified-zero")
                    expect(rows.first).to_contain_text("已设置")
                    expect(rows.nth(1)).to_contain_text("gpt-6-astra")
                    expect(inspector.get_by_role("button", name="上一页", exact=True)).to_be_disabled()
                    if kind == "model":
                        rows.first.click()
                    inspector.get_by_role("button", name="下一页", exact=True).click()
                    expect(rows).to_have_count(10)
                    expect(inspector.get_by_text("共 26 条 · 第 2 / 3 页", exact=True)).to_be_visible()
                    if kind == "model":
                        expect(inspector.locator("details[open]")).to_have_count(0)
                    inspector.get_by_role("button", name="下一页", exact=True).click()
                    expect(rows).to_have_count(6)
                    expect(inspector.get_by_text("共 26 条 · 第 3 / 3 页", exact=True)).to_be_visible()
                    expect(inspector.get_by_role("button", name="下一页", exact=True)).to_be_disabled()
                    inspector.get_by_role("button", name="上一页", exact=True).click()
                    search = inspector.get_by_role("searchbox", name=f"搜索{title}", exact=True)
                    search.fill("INHERIT-2")
                    expect(rows).to_have_count(4)
                    expect(rows.first).to_contain_text("inherit-20")
                    expect(inspector.get_by_text("共 4 条 · 第 1 / 1 页", exact=True)).to_be_visible()
                    search.fill("no-such-model")
                    expect(rows).to_have_count(0)
                    expect(inspector.get_by_text("没有匹配的模型或规则。", exact=True)).to_be_visible()
                    expect(inspector.get_by_text("共 0 条 · 第 1 / 1 页", exact=True)).to_be_visible()
                    search.fill("")
                    expect(rows).to_have_count(10)
                    expect(rows.first).to_contain_text("modified-zero")
                    inspector.get_by_role("tab", name=re.compile("Synthetic pricing group")).click()
                    expect(rows).to_have_count(2)
                    inspector.get_by_role("tab", name=re.compile("Synthetic unselected group")).click()
                    expect(rows).to_have_count(10)
                    expect(rows.first).to_contain_text("modified-zero")
                page.set_viewport_size({"width": 390, "height": 844})
                inspector.locator(".modal-box").screenshot(path=str(OUTPUT / f"current-{kind}-390.png"))
                page.set_viewport_size({"width": 1440, "height": 1000})
                inspector.locator(".modal-box").screenshot(path=str(OUTPUT / f"current-{kind}-desktop.png"))
                inspector.get_by_role("button", name="关闭", exact=True).first.click()
            card.get_by_role("button", name="查看当前FAST 倍率", exact=True).click()
            inspector = page.get_by_role("dialog", name="当前FAST 倍率", exact=True)
            expect(inspector.get_by_text("3×", exact=True)).to_be_visible()
            inspector.get_by_role("button", name="关闭", exact=True).first.click()
            expect(model_multiplier).to_have_value("5")
            model_multiplier.fill("1.8")
            assert sum(call["method"] == "PUT" for call in UPSTREAM["calls"]) == writes_before
            card.get_by_role("button", name="写入 Sub2API 分组计费", exact=True).click()
            selection = page.get_by_role("dialog", name="选择目标分组并写入", exact=True)
            selected_group = selection.get_by_role("checkbox", name="Synthetic pricing group ID 7", exact=True)
            expect(selected_group).to_be_checked()
            expect(selection.get_by_role("checkbox", name="Synthetic unselected group ID 8", exact=True)).not_to_be_checked()
            selected_group.uncheck()
            expect(selection.get_by_role("button", name="确认写入", exact=True)).to_be_disabled()
            selection.locator(".modal-action").get_by_role("button", name="取消", exact=True).click()
            assert sum(call["method"] == "PUT" for call in UPSTREAM["calls"]) == writes_before
            check_rule_layout(page, card, OUTPUT)
            original = database_snapshot()
            multiplier = model_multiplier
            multiplier.fill("0")
            card.get_by_role("button", name="写入 Sub2API 分组计费", exact=True).click()
            expect(card.get_by_role("alert").last).to_contain_text("0.01")
            multiplier.fill("2")
            UPSTREAM["pricing_fail_next"] = True
            card.get_by_role("button", name="写入 Sub2API 分组计费", exact=True).click()
            for status in (502, 200):
                with page.expect_response(lambda response: response.url.endswith("/api/settings/upstream-pricing/apply")) as changed:
                    selection.get_by_role("button", name="确认写入", exact=True).click()
                assert changed.value.status == status
                expect(card.get_by_role("button", name="写入 Sub2API 分组计费", exact=True)).to_be_enabled()
                assert database_snapshot() == original
            price = next(row for row in UPSTREAM["group"]["model_pricing"] if row["models"] == ["gpt-6-astra"])
            assert price["input_price"] == 0.00001 and price["fast_multiplier"] == 2
            assert UPSTREAM["group"]["rate_multiplier"] == 0.7
            card.screenshot(path=str(OUTPUT / "settings-card.png"))
            page.goto(FRONTEND_URL + "/observations")
            table = page.locator("table").first
            expect(table.get_by_role("button", name="已修正", exact=True)).to_have_count(1)
            table.get_by_role("button", name="已修正", exact=True).click()
            dialog = page.locator("dialog[open]").last
            expect(dialog.get_by_text("上游 Sub2API 已修正", exact=True)).to_be_visible()
            expect(dialog.get_by_role("button", name="从上游补算此区间", exact=True)).to_have_count(0)
            page.screenshot(path=str(OUTPUT / "upstream-observation.png"))
            dialog.locator(".modal-action").get_by_role("button", name="关闭", exact=True).click()
            expect(table.get_by_role("button", name="未计算", exact=True)).to_have_count(2)
            legacy_row = table.locator("tbody tr").filter(has=page.get_by_role("button", name="已有明细", exact=True))
            UPSTREAM["fail_next"] = True
            with page.expect_response(lambda response: response.url.endswith(f"/{SEED_IDS[2]}/fast-correction/calculate")) as failed:
                legacy_row.get_by_role("button", name="未计算", exact=True).click()
            assert failed.value.status == 502
            expect(legacy_row.get_by_role("button", name="未计算", exact=True)).to_be_enabled()
            snapshot = database_snapshot()
            assert SEED_IDS[2] not in snapshot["captures"]
            assert snapshot["observations"][SEED_IDS[2]]["fast"] == 25
            legacy_row.get_by_role("button", name="已有明细", exact=True).click()
            latest_before = snapshot["observations"][SEED_IDS[6]]["cost"]
            with page.expect_response(lambda response: response.url.endswith(f"/{SEED_IDS[2]}/fast-correction/calculate")) as repaired:
                dialog.get_by_role("button", name="从上游补算此区间", exact=True).click()
            assert repaired.value.status == 200
            assert repaired.value.json()["data"]["correction_total_usd"] == 12.5
            expect(dialog.get_by_role("button", name="从上游补算此区间", exact=True)).to_have_count(0)
            assert database_snapshot()["observations"][SEED_IDS[6]]["cost"] == latest_before - D("12.5")
            page.screenshot(path=str(OUTPUT / "legacy-interval-backfilled.png"))
            dialog.locator(".modal-action").get_by_role("button", name="关闭", exact=True).click()
            with page.expect_response(lambda response: response.url.endswith(f"/{SEED_IDS[3]}/fast-correction/calculate")) as repaired_missing:
                table.get_by_role("button", name="未计算", exact=True).click()
            assert repaired_missing.value.status == 200
            expect(table.get_by_role("button", name="未计算", exact=True)).to_have_count(0)
            assert database_snapshot()["observations"][SEED_IDS[6]]["cost"] == latest_before
            page.goto(FRONTEND_URL + "/settings")
            expect(heading).to_be_visible()
            page.get_by_role("button", name="查看公告", exact=True).click()
            announcement_dialog = page.locator("dialog[open]").last
            apply_announcement = announcement_dialog.get_by_role("button", name="一键应用修正", exact=True)
            expect(apply_announcement).to_be_visible()
            announcement_dialog.locator(".modal-box").screenshot(path=str(OUTPUT / "announcement-actions-desktop.png"), animations="disabled")
            page.set_viewport_size({"width": 390, "height": 844})
            announcement_dialog.get_by_role("button", name="一键开启", exact=True).scroll_into_view_if_needed()
            announcement_dialog.locator(".modal-box").screenshot(path=str(OUTPUT / "announcement-actions-390.png"), animations="disabled")
            page.set_viewport_size({"width": 1440, "height": 1000})
            writes_before_announcement = sum(call["method"] == "PUT" for call in UPSTREAM["calls"])
            apply_announcement.click()
            selection = page.get_by_role("dialog", name="选择目标分组并写入", exact=True)
            expect(selection.get_by_role("button", name="确认写入", exact=True)).to_be_disabled()
            selection.locator(".modal-action").get_by_role("button", name="取消", exact=True).click()
            expect(apply_announcement).to_be_visible()
            assert sum(call["method"] == "PUT" for call in UPSTREAM["calls"]) == writes_before_announcement
            with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH") as enabled:
                announcement_dialog.get_by_role("button", name="一键开启", exact=True).click()
            assert enabled.value.status == 200
            expect(announcement_dialog.get_by_role("button", name="已开启", exact=True)).to_be_disabled()
            expect(page.get_by_role("checkbox", name="启用自动应用", exact=True, include_hidden=True)).to_be_checked()
            apply_announcement.click()
            selection.get_by_role("checkbox", name="Synthetic pricing group ID 7", exact=True).check()
            selection.screenshot(path=str(OUTPUT / "announcement-select-groups.png"))
            with page.expect_response(lambda response: response.url.endswith("/api/settings/upstream-pricing/apply")) as applied:
                selection.get_by_role("button", name="确认写入", exact=True).click()
            assert applied.value.status == 200
            expect(selection).not_to_be_visible()
            expect(apply_announcement).to_have_count(0)
            assert UPSTREAM["group"]["long_context_pricing_enabled"] is False
            for row in UPSTREAM["group"]["model_pricing"]:
                for model in row["models"]:
                    assert row["fast_multiplier"] == (2 if model.startswith("gpt-6") else 2.5)
            assert next(row for row in UPSTREAM["group"]["model_pricing"] if row["models"] == ["gpt-6-astra"])["input_price"] == 0.000009
            page.reload()
            expect(heading).to_be_visible()
            page.get_by_role("button", name="查看公告", exact=True).click()
            expect(page.get_by_role("button", name="一键应用修正", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="已开启", exact=True)).to_be_disabled()
            dialog.get_by_role("button", name="撤回上游计费配置", exact=True).click()
            with page.expect_response(lambda response: response.url.endswith("/api/settings/upstream-pricing/revert")) as reverted:
                page.get_by_role("button", name="确认撤回", exact=True).click()
            assert reverted.value.status == 200
            expect(page.locator("dialog[open]").last.get_by_text(re.compile("上游计费已撤回"))).to_be_visible()
            assert UPSTREAM["group"] == GROUP_BASELINE
            page.screenshot(path=str(OUTPUT / "announcement-reverted.png"))
            expect(page.get_by_role("button", name="一键应用修正", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="撤回上游计费配置", exact=True)).to_be_disabled()
            page.locator("dialog[open]").last.get_by_role("button", name="关闭公告", exact=True).first.click()
            expect(card.get_by_role("button", name="撤回至接管前值", exact=True)).to_be_disabled()
            assert not errors, errors
            (OUTPUT / "browser-results.json").write_text(json.dumps({
                "passed": True, "page_errors": errors,
                "checks": ["ten-row pagination", "previous/next boundaries", "case-insensitive search",
                           "explicit overrides sorted first", "page reset on search/group change", "detail state isolation",
                           "write-time group selection", "read-only category dialogs", "independent group tabs",
                           "live values ignore unsaved draft", "upstream write confirmation", "failed write and retry", "frozen history unchanged",
                           "upstream observation explanation", "historical backfill failure and retry",
                           "frozen suffix replay", "announcement undo", "cross-component status refresh",
                           "announcement apply cancel and confirm", "one-shot persists across refresh and undo",
                           "enable recommendations from announcement"],
                "upstream_calls": UPSTREAM["calls"],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            (OUTPUT / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
            page.screenshot(path=str(OUTPUT / "failure.png"), full_page=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticUpstream)
    worker = Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    processes = []
    try:
        seed(f"http://127.0.0.1:{upstream.server_port}")
        processes.append(subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{BACKEND_PORT}", "--noreload"], cwd=ROOT / "backend", stdout=open(OUTPUT / "django.log", "w"), stderr=subprocess.STDOUT))
        processes.append(subprocess.Popen(["node", str(ROOT / "frontend/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", str(FRONTEND_PORT)], cwd=ROOT / "frontend", stdout=open(OUTPUT / "vite.log", "w"), stderr=subprocess.STDOUT))
        wait_for_server(f"http://127.0.0.1:{BACKEND_PORT}/api/auth/client-config")
        wait_for_server(FRONTEND_URL + "/login")
        smoke()
    finally:
        upstream.shutdown()
        upstream.server_close()
        worker.join(timeout=5)
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
