"""CPA carpool browser regression against an isolated synthetic database.

Run with backend/.venv/bin/python after installing Playwright and Chromium.
Never reads the project's data directory or contacts a real upstream.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.mkdtemp(prefix="sub2pool-cpa-browser-"))
os.environ.update(
    DJANGO_SETTINGS_MODULE="pinche.settings",
    DJANGO_DEBUG="true",
    PINCH_DATA_DIR=str(OUTPUT / "database"),
    WEBRTC_IP_COLLECTION_ENABLED="false",
    VITE_API_TARGET="http://127.0.0.1:8367",
)
sys.path.insert(0, str(ROOT / "backend"))
import django

django.setup()
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from monitor.announcements import ANNOUNCEMENTS
from monitor.cpa.participants import record_contract
from monitor.cpa.usage import _api_key_identity
from monitor.models import (
    AnnouncementRead,
    AppSettings,
    CPAAPIKey,
    CPAKeyBinding,
    CPAUsageEvent,
    CPAAccountCollectionInterval,
    Participant,
    PoolParticipant,
    Observation,
    SystemUserPageAccess,
)
from monitor.replay import rebuild_account
from monitor.secrets import encrypt_secret
from monitor.tests.helpers import create_cpa_account, create_monitored_account
from playwright.sync_api import sync_playwright, expect


def seed():
    call_command("migrate", verbosity=0)
    config = AppSettings.load()
    config.monitoring_enabled = False
    config.timezone = "Asia/Shanghai"
    config.cpa_base_url = "http://127.0.0.1:9"
    config.cpa_management_key_encrypted = encrypt_secret("synthetic-management-key")
    config.cpa_model_pricing = {
        "gpt-test": {"input": "10", "cached_input": "1", "output": "30"}
    }
    config.save()
    admin = get_user_model().objects.create_superuser(
        "owner", "owner@example.test", "Synthetic-CPA-Review-2026!"
    )
    viewer = get_user_model().objects.create_user(
        "alice", password="Synthetic-CPA-Review-2026!"
    )
    for user in (admin, viewer):
        for announcement in ANNOUNCEMENTS:
            AnnouncementRead.objects.create(
                user=user, announcement_code=announcement.code, read_at=timezone.now()
            )
    for page in ("dashboard", "statistics", "participants"):
        SystemUserPageAccess.objects.create(user=viewer, page_code=page)
    account = create_cpa_account(name="CPA 浏览器验收")
    sub = create_monitored_account(7, name="Sub2API 回归账号")
    alice = Participant.objects.create(name="Alice", sub2api_user_id=51)
    bob = Participant.objects.create(name="Bob")
    alice.authorized_users.add(viewer)
    account.authorized_users.add(viewer)
    start = timezone.now().replace(microsecond=0) - timedelta(hours=2)
    account.created_at = start
    account.save(update_fields=["created_at"])
    for person in (alice, bob):
        PoolParticipant.objects.create(
            pool=account.pool, participant=person, share_percent=50
        )
    record_contract(account, start)
    CPAAccountCollectionInterval.objects.create(
        account=account, session_key="browser", connected_at=start
    )
    for index, (person, raw) in enumerate(
        ((alice, "alice-key-1111"), (bob, "bob-key-2222"), (None, "unclaimed-key-4444"))
    ):
        digest, hint = _api_key_identity(raw)
        if person:
            key = CPAAPIKey.objects.create(key_hash=digest, hint=hint)
            CPAKeyBinding.objects.create(key=key, participant=person, started_at=start)
        CPAUsageEvent.objects.create(
            account=account,
            event_fingerprint=f"browser-{index}",
            occurred_at=start + timedelta(minutes=10 + index),
            request_id=f"request-{index}",
            api_key_hash=digest,
            api_key_hint=hint,
            model="gpt-test",
            input_tokens=1_000_000,
            total_tokens=1_000_000,
            latency_ms=1200,
            ttft_ms=240,
            failed=index == 1,
        )
    for when, percent, cost in ((start, 0, 0), (start + timedelta(hours=1), 10, 30)):
        Observation.objects.create(
            account_id=account.fact_key,
            observed_at=when,
            window_seconds=604800,
            upstream_resets_at=start + timedelta(days=7),
            upstream_used_percent=percent,
            raw_selected_total_cost=cost,
            selected_total_cost=cost,
            total_standard_cost=cost,
            total_actual_cost=cost,
            effective_usd_per_percent=Decimal("10"),
            cost_window_started_at=start,
            cost_window_ended_at=when,
            raw_window={"provider": "cpa"},
        )
    rebuild_account(account.fact_key, config)
    return account.id, sub.id, alice.id, start


def wait(url):
    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Local test server did not start: {url}")


def login(page, username):
    page.goto("http://127.0.0.1:5179/login")
    page.get_by_label("用户名", exact=True).fill(username)
    page.get_by_label("密码", exact=True).fill("Synthetic-CPA-Review-2026!")
    page.get_by_role("button", name="登录", exact=True).click()
    page.wait_for_url("http://127.0.0.1:5179/")


def smoke(account_id, sub_id, alice_id, start):
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_default_timeout(15000)
        try:
            login(page, "owner")
            expect(page.get_by_test_id("cpa-pool-summary")).to_be_visible()
            page.goto("http://127.0.0.1:5179/participants")
            page.get_by_label("选择参与者渠道").select_option("cpa")
            manager = page.get_by_test_id("cpa-key-manager")
            expect(manager).to_be_visible()
            manager.get_by_label("参与者", exact=True).select_option(str(alice_id))
            options = (
                manager.get_by_label("已采集 Key").locator("option").all_text_contents()
            )
            option = next(label for label in options if "4444" in label)
            manager.get_by_label("已采集 Key").select_option(label=option)
            manager.get_by_label("Key 备注", exact=True).fill("Laptop")
            manager.get_by_role("button", name="绑定 Key", exact=True).click()
            expect(manager.get_by_text("Key 已绑定", exact=False)).to_be_visible()
            row = manager.get_by_role("row").filter(
                has=page.get_by_label("Key 4444 备注")
            )
            row.get_by_role("button", name="历史认领").click()
            dialog = page.locator("#cpa-claim-dialog")
            expect(dialog).to_be_visible()
            dialog.get_by_label("开始时间", exact=True).fill(
                start.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%dT%H:%M")
            )
            dialog.get_by_role("button", name="预览历史用量").click()
            expect(dialog.get_by_text("1 次请求", exact=False)).to_be_visible()
            page.screenshot(path=str(OUTPUT / "claim-preview.png"), full_page=True)
            dialog.get_by_role("button", name="确认认领这些请求").click()
            expect(dialog).not_to_be_visible()
            expect(manager.get_by_text("历史请求已认领", exact=False)).to_be_visible()
            page.get_by_role("button", name="刷新 CPA 额度").click()
            expect(
                page.get_by_test_id("cpa-pool-summary")
                .get_by_role("row")
                .filter(has_text="Alice")
            ).to_contain_text("$20.00")
            page.goto("http://127.0.0.1:5179/allocation")
            page.get_by_label("选择额度分配渠道").select_option("cpa")
            expect(
                page.get_by_text("CPA 浏览器验收", exact=False).first
            ).to_be_visible()
            page.screenshot(path=str(OUTPUT / "allocation-desktop.png"), full_page=True)
            page.goto("http://127.0.0.1:5179/statistics")
            page.get_by_label("选择监控账号").select_option(str(account_id))
            expect(page.get_by_test_id("cpa-requests")).to_contain_text("request-1")
            page.screenshot(path=str(OUTPUT / "requests-admin.png"), full_page=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1050})
            rider = context.new_page()
            rider.on("pageerror", lambda error: errors.append(str(error)))
            login(rider, "alice")
            expect(rider.get_by_test_id("cpa-pool-summary")).to_contain_text(
                "Alice的额度"
            )
            expect(rider.get_by_test_id("cpa-pool-summary")).to_contain_text("Bob")
            rider.goto("http://127.0.0.1:5179/statistics")
            requests = rider.get_by_test_id("cpa-requests")
            expect(requests).to_contain_text("共 2 条")
            expect(requests).not_to_contain_text("request-1")
            expect(requests).to_contain_text("request-0")
            expect(requests).to_contain_text("request-2")
            requests.get_by_label("状态", exact=True).select_option("true")
            requests.get_by_role("button", name="查询", exact=True).click()
            expect(requests).to_contain_text("共 0 条")
            requests.get_by_label("状态", exact=True).select_option("")
            requests.get_by_role("button", name="查询", exact=True).click()
            expect(requests).to_contain_text("共 2 条")
            rider.screenshot(path=str(OUTPUT / "requests-member.png"), full_page=True)
            for target, name in ((page, "admin"), (rider, "member")):
                target.set_viewport_size({"width": 390, "height": 844})
                target.wait_for_function(
                    "document.documentElement.scrollWidth <= innerWidth + 2"
                )
                target.evaluate("window.scrollTo(0, 0)")
                target.screenshot(
                    path=str(OUTPUT / f"requests-{name}-mobile.png"), full_page=True, animations="disabled"
                )
                assert target.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth + 2"
                ), f"{name} page overflows mobile viewport"
            assert not errors, errors
            (OUTPUT / "results.json").write_text(
                json.dumps(
                    {
                        "passed": True,
                        "page_errors": errors,
                        "checks": [
                            "admin binds observed key",
                            "history preview and apply",
                            "CPA allocation channel",
                            "quota recomputed after claim",
                            "member sees peer totals",
                            "member requests scoped to own keys",
                            "status filter",
                            "390px and desktop layouts",
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        except Exception:
            page.screenshot(path=str(OUTPUT / "failure.png"), full_page=True)
            (OUTPUT / "failure-page.txt").write_text(page.locator("body").inner_text())
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    processes = []
    print(str(OUTPUT), flush=True)
    try:
        fixture = seed()
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "manage.py",
                    "runserver",
                    "127.0.0.1:8367",
                    "--noreload",
                ],
                cwd=ROOT / "backend",
                stdout=open(OUTPUT / "django.log", "w"),
                stderr=subprocess.STDOUT,
            )
        )
        processes.append(
            subprocess.Popen(
                ["node_modules/.bin/vite", "--host", "127.0.0.1", "--port", "5179"],
                cwd=ROOT / "frontend",
                stdout=open(OUTPUT / "vite.log", "w"),
                stderr=subprocess.STDOUT,
            )
        )
        wait("http://127.0.0.1:8367/api/auth/client-config")
        wait("http://127.0.0.1:5179/login")
        smoke(*fixture)
        print("CPA browser checks passed", flush=True)
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
