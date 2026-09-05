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
import urllib.request

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
from monitor.models import AppSettings, Observation, AnnouncementRead  # noqa: E402
from monitor.tests.helpers import create_monitored_account, create_participant  # noqa: E402
from monitor.tests.billing_correction.test_corrections import log  # noqa: E402
from monitor.fast_correction.domain import aggregate_fast_logs  # noqa: E402
from monitor.fast_correction.persistence import apply_fast_interval  # noqa: E402
from monitor.fast_correction.rules import FastCorrectionRuleSet  # noqa: E402
from monitor.replay import rebuild_account  # noqa: E402
from monitor.announcements import ANNOUNCEMENTS  # noqa: E402
from datetime import timedelta  # noqa: E402
from decimal import Decimal as D  # noqa: E402
from playwright.sync_api import sync_playwright, expect  # noqa: E402


def seed():
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
            page.goto("http://127.0.0.1:5173/settings")
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
            assert not errors, errors
            (OUTPUT / "browser-results.json").write_text(json.dumps({
                "passed": True, "page_errors": errors,
                "checks": ["single settings card", "all three editors", "rule reorder/cancel", "multiplier validation", "real settings PATCH/local replay", "single observation column", "negative correction breakdown", "390px modal layout"],
            }, ensure_ascii=False, indent=2))
        except Exception:
            page.screenshot(path=str(OUTPUT / "failure.png"), full_page=True)
            (OUTPUT / "failure-page.txt").write_text(page.locator("body").inner_text())
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    seed()
    processes = []
    try:
        processes.append(subprocess.Popen([sys.executable, "manage.py", "runserver", "127.0.0.1:8000", "--noreload"], cwd=ROOT / "backend", stdout=open(OUTPUT / "django.log", "w"), stderr=subprocess.STDOUT))
        processes.append(subprocess.Popen(["node_modules/.bin/vite", "--host", "127.0.0.1"], cwd=ROOT / "frontend", stdout=open(OUTPUT / "vite.log", "w"), stderr=subprocess.STDOUT))
        wait_for_server("http://127.0.0.1:8000/api/auth/client-config")
        wait_for_server("http://127.0.0.1:5173/login")
        smoke()
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
