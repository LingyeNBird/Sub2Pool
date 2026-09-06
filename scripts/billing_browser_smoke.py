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

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ.get("BILLING_REVIEW_OUTPUT", ROOT / "billing-review-output")).resolve()
# The fixture MUST live below the designated isolated output directory.
OUTPUT.mkdir(parents=True, exist_ok=True)
os.environ.update(DJANGO_SETTINGS_MODULE="pinche.settings", DJANGO_DEBUG="true",
                  PINCH_DATA_DIR=str(OUTPUT / "database"), WEBRTC_IP_COLLECTION_ENABLED="false")
sys.path.insert(0, str(ROOT / "backend"))
import django  # noqa: E402
django.setup()
from django.core.management import call_command  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402
from monitor.models import AppSettings, Observation, AnnouncementRead, ObservationBillingCapture, BillingUsageFact  # noqa: E402
from monitor.tests.helpers import create_monitored_account, create_participant  # noqa: E402
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


UPSTREAM = {"rows": [], "calls": [], "fail_next": False}
SEED_IDS = {}


class SyntheticUpstream(BaseHTTPRequestHandler):
    """Only a local read-only usage API; never forwards requests anywhere."""
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        path = urlparse(self.path).path
        UPSTREAM["calls"].append({"path": path, "query": query})
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
    config.monitoring_enabled = False
    config.sub2api_base_url = upstream_url
    config.sub2api_admin_token_encrypted = encrypt_secret("Synthetic-Sub2API-Local-Only")
    config.weekly_quota_model = "constant_average"
    config.save()
    create_monitored_account()
    create_participant(name="Synthetic reviewer", sub2api_user_id=51, share_percent=100)
    Observation.objects.all().delete()
    start = timezone.now().replace(microsecond=0) - timedelta(hours=6)
    for index in range(1, 7):
        at = start + timedelta(hours=index)
        observation = Observation.objects.create(
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
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("BILLING_BROWSER_EXECUTABLE") or None,
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_default_timeout(15000)
        try:
            page.goto("http://127.0.0.1:5173/login")
            page.get_by_label("用户名", exact=True).fill("billing-reviewer")
            page.get_by_label("密码", exact=True).fill("Synthetic-Local-Review-2026!")
            page.get_by_role("button", name="登录", exact=True).click()
            page.wait_for_url("http://127.0.0.1:5173/")
            with page.expect_response(lambda response: response.url.endswith("/api/settings/openai-accounts")) as discovered:
                page.goto("http://127.0.0.1:5173/settings")
            assert discovered.value.status == 200
            assert len(UPSTREAM["calls"]) == 1
            assert UPSTREAM["calls"][0]["path"] == "/api/v1/admin/accounts"
            discovery_calls = list(UPSTREAM["calls"])
            heading = page.get_by_role("heading", name="计费修正", exact=True)
            expect(heading).to_be_visible()
            card = page.locator("section").filter(has=heading)
            expect(card).to_have_count(1)
            expect(card.get_by_role("checkbox")).to_have_count(3)
            configure = card.get_by_role("button", name=re.compile("配置规则"))
            expect(configure).to_have_count(3)
            card.screenshot(path=str(OUTPUT / "settings-card.png"))
            configure.nth(0).click()
            dialog = page.locator("dialog[open]").last
            expect(dialog.get_by_role("heading", name="FAST 模型修正规则")).to_be_visible()
            expect(dialog.get_by_label("模型匹配", exact=True)).to_have_count(1)
            dialog.get_by_role("button", name="取消", exact=True).click()
            configure.nth(1).click()
            expect(dialog.get_by_role("heading", name="双倍倍率修正规则")).to_be_visible()
            matches = dialog.get_by_label("模型匹配", exact=True)
            expect(matches).to_have_count(2)
            expect(matches.nth(0)).to_have_value("gpt-5.6*")
            expect(matches.nth(1)).to_have_value("gpt-6*")
            page.screenshot(path=str(OUTPUT / "long-context-desktop.png"))
            dialog.get_by_role("button", name="下移规则", exact=True).first.click()
            expect(matches.nth(0)).to_have_value("gpt-6*")
            dialog.get_by_role("button", name="取消", exact=True).click()
            configure.nth(1).click()
            expect(dialog.get_by_label("模型匹配", exact=True).first).to_have_value("gpt-5.6*")
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(OUTPUT / "long-context-mobile.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            dialog.get_by_role("button", name="取消", exact=True).click()
            page.set_viewport_size({"width": 1440, "height": 1000})
            configure.nth(2).click()
            multiplier = dialog.get_by_label("计费倍率", exact=True)
            expect(multiplier).to_have_value("1.8")
            multiplier.fill("0")
            dialog.get_by_role("button", name="保存规则并重算", exact=True).click()
            expect(dialog.get_by_role("alert")).to_contain_text("0.01")
            multiplier.fill("1")
            with page.expect_response(lambda response: response.url.endswith("/api/settings") and response.request.method == "PATCH") as changed:
                dialog.get_by_role("button", name="保存规则并重算", exact=True).click()
            assert changed.value.status == 200
            assert UPSTREAM["calls"] == discovery_calls  # Policy save is entirely local.
            expect(page.locator("dialog[open]")).to_have_count(0)
            page.goto("http://127.0.0.1:5173/observations")
            expect(page.get_by_role("columnheader", name="修正合计", exact=True)).to_have_count(1)
            table = page.locator("table").first
            header_names = table.locator("thead th").all_text_contents()
            assert not any(name.strip() in ["FAST 修正", "长上下文修正", "模型倍率修正"] for name in header_names)
            index = next(i for i, value in enumerate(header_names) if value.strip() == "修正合计")
            button = table.locator("tbody tr").first.locator("td").nth(index).get_by_role("button").first
            expect(button).to_contain_text("37.50")
            button.click()
            expect(dialog.get_by_role("heading", name="修正合计明细")).to_be_visible()
            for label in ("FAST 修正", "长上下文修正", "模型倍率修正"):
                expect(dialog.get_by_text(label, exact=True).first).to_be_visible()
            expect(dialog.get_by_text("−$37.50", exact=True).first).to_be_visible()
            page.screenshot(path=str(OUTPUT / "observation-corrections-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(OUTPUT / "observation-corrections-mobile.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            dialog.locator(".modal-action").get_by_role("button", name="关闭", exact=True).click()
            page.set_viewport_size({"width": 1440, "height": 1000})
            expect(table.get_by_role("button", name="未计算", exact=True)).to_have_count(2)
            legacy_row = table.locator("tbody tr").filter(has=page.get_by_role("button", name="已有明细", exact=True))
            expect(legacy_row).to_have_count(1)
            # Separate backfill reads from the settings page's account discovery.
            assert UPSTREAM["calls"] == discovery_calls
            backfill_start = len(UPSTREAM["calls"])
            is_list_response = lambda response: urlparse(response.url).path == "/api/observations" and response.request.method == "GET"
            # The real backend makes a failing HTTP read; old data must survive.
            UPSTREAM["fail_next"] = True
            with page.expect_response(lambda response: response.url.endswith(f"/{SEED_IDS[2]}/fast-correction/calculate")) as failed:
                legacy_row.get_by_role("button", name="未计算", exact=True).click()
            assert failed.value.status == 502
            expect(page.get_by_text(re.compile("Synthetic upstream outage"))).to_be_visible()
            expect(legacy_row.get_by_role("button", name="未计算", exact=True)).to_be_enabled()
            snapshot = database_snapshot()
            assert SEED_IDS[2] not in snapshot["captures"]
            assert snapshot["observations"][SEED_IDS[2]]["fast"] == 25
            legacy_row.get_by_role("button", name="已有明细", exact=True).click()
            repair = dialog.get_by_role("button", name="从上游补算此区间", exact=True)
            expect(repair).to_be_visible()
            latest_before = snapshot["observations"][SEED_IDS[6]]["cost"]
            with page.expect_response(is_list_response) as refreshed_legacy:
                with page.expect_response(lambda response: response.url.endswith(f"/{SEED_IDS[2]}/fast-correction/calculate")) as repaired:
                    repair.click()
            assert repaired.value.status == 200
            assert repaired.value.json()["data"]["correction_total_usd"] == -37.5
            expect(dialog.get_by_text("−$37.50", exact=True).first).to_be_visible()
            expect(dialog.get_by_role("button", name="从上游补算此区间", exact=True)).to_have_count(0)
            page.screenshot(path=str(OUTPUT / "legacy-interval-backfilled.png"))
            latest_after = database_snapshot()["observations"][SEED_IDS[6]]["cost"]
            assert latest_after == latest_before - D("62.5")
            refreshed_rows = refreshed_legacy.value.json()["data"]["items"]
            assert next(row for row in refreshed_rows if row["id"] == SEED_IDS[6])["selected_total_cost"] == float(latest_after)
            dialog.locator(".modal-action").get_by_role("button", name="关闭", exact=True).click()
            expect(table.get_by_role("button", name="未计算", exact=True)).to_have_count(1)
            with page.expect_response(is_list_response) as refreshed_new:
                with page.expect_response(lambda response: response.url.endswith(f"/{SEED_IDS[3]}/fast-correction/calculate")) as new_interval:
                    table.get_by_role("button", name="未计算", exact=True).click()
            assert new_interval.value.status == 200
            expect(table.get_by_role("button", name="未计算", exact=True)).to_have_count(0)
            expect(page.get_by_text("已补算 1 个区间的修正合计，并更新相关结论", exact=True)).to_be_visible()
            expect(table.locator("tbody tr")).to_have_count(6)
            snapshot = database_snapshot()
            assert len(snapshot["captures"]) == snapshot["facts"] == 6
            refreshed_rows = refreshed_new.value.json()["data"]["items"]
            latest_row = next(row for row in refreshed_rows if row["id"] == SEED_IDS[6])
            assert latest_row["selected_total_cost"] == float(latest_after - D("37.5"))
            # Check the adopted rate actually rendered from the refreshed list.
            rate_text = f"${latest_row['effective_usd_per_percent']:.2f}"
            expect(table.locator("tbody tr").first.locator("td").nth(8)).to_have_text(rate_text)
            backfill_calls = UPSTREAM["calls"][backfill_start:]
            assert len(backfill_calls) == 3, backfill_calls
            assert all(call["path"] == "/api/v1/admin/usage" for call in backfill_calls)
            page.screenshot(path=str(OUTPUT / "all-intervals-backfilled.png"))
            assert not errors, errors
            (OUTPUT / "upstream-read-evidence.json").write_text(json.dumps(UPSTREAM["calls"], ensure_ascii=False, indent=2))
            (OUTPUT / "browser-results.json").write_text(json.dumps({
                "passed": True, "page_errors": errors, "upstream_read_count": len(UPSTREAM["calls"]),
                "backfill_read_count": len(backfill_calls), "account_discovery_read_count": len(discovery_calls),
                "checks": ["single settings card", "all three editors", "rule reorder/cancel", "multiplier validation", "real settings PATCH/local replay", "single observation column", "negative correction breakdown", "390px modal layout", "legacy FAST-only uncalculated link", "failed upstream read preserves data", "detail backfill and retry", "new missing interval backfill", "suffix refresh", "only local read-only usage endpoint"],
            }, ensure_ascii=False, indent=2))
        except Exception:
            # Save the real assertion/navigation failure even if Chromium has
            # lost its execution context while collecting screenshots.
            (OUTPUT / "failure.txt").write_text(traceback.format_exc())
            try:
                (OUTPUT / "failure-layout.json").write_text(json.dumps(page.evaluate("""() => ({
                    viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth,
                    overflow: [...document.querySelectorAll('body *')].map(element => {
                        const rect = element.getBoundingClientRect();
                        return {tag: element.tagName, classes: element.className,
                            width: rect.width, left: rect.left, right: rect.right,
                            text: element.textContent?.slice(0, 120)};
                    }).filter(rect => rect.width && (rect.right > innerWidth + 1 || rect.left < -1))
                })"""), ensure_ascii=False, indent=2))
                page.screenshot(path=str(OUTPUT / "failure.png"), full_page=True)
                (OUTPUT / "failure-page.txt").write_text(page.locator("body").inner_text())
            except Exception:
                (OUTPUT / "diagnostic-failure.txt").write_text(traceback.format_exc())
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
        processes.append(subprocess.Popen([sys.executable, "manage.py", "runserver", "127.0.0.1:8000", "--noreload"], cwd=ROOT / "backend", stdout=open(OUTPUT / "django.log", "w"), stderr=subprocess.STDOUT))
        processes.append(subprocess.Popen(["node_modules/.bin/vite", "--host", "127.0.0.1"], cwd=ROOT / "frontend", stdout=open(OUTPUT / "vite.log", "w"), stderr=subprocess.STDOUT))
        wait_for_server("http://127.0.0.1:8000/api/auth/client-config")
        wait_for_server("http://127.0.0.1:5173/login")
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
