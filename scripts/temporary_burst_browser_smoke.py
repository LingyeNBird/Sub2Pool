"""Exercise the feature through real Django/Vue and a loopback-only synthetic wallet."""

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import billing_browser_smoke as harness
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.utils import timezone
from monitor.balance_operations import auto_apply_recommendations
from monitor.engine import _rebuild_capture
from monitor.history_state import LeaseGuard
from monitor.models import AppSettings, Participant, PoolParticipant
from monitor.models.temporary_burst import TemporaryBurstCycle
from monitor.secrets import encrypt_secret
from monitor.tests.helpers import create_monitored_account
from monitor.tests.test_temporary_burst import record
from playwright.sync_api import expect, sync_playwright

BALANCES = {51: 80.0, 52: 80.0, 53: 80.0}
WRITES = []


class Wallet(BaseHTTPRequestHandler):
    def do_POST(self):
        match = re.fullmatch(r"/api/v1/admin/users/(\d+)/balance", self.path)
        assert match and self.headers.get("x-api-key") == "Synthetic-Burst-Local-Only"
        user = int(match[1])
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert data["operation"] in {"set", "subtract"}
        BALANCES[user] = (
            data["balance"]
            if data["operation"] == "set"
            else BALANCES[user] - data["balance"]
        )
        WRITES.append((user, BALANCES[user]))
        self.reply({"id": user, "balance": BALANCES[user]})

    def do_GET(self):
        match = re.fullmatch(r"/api/v1/admin/users/(\d+)", self.path)
        assert match
        user = int(match[1])
        self.reply({"id": user, "balance": BALANCES[user], "frozen_balance": 0})

    def reply(self, data):
        body = json.dumps({"code": 0, "data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def seed(url):
    call_command("migrate", verbosity=0)
    get_user_model().objects.create_superuser(
        username="burst-reviewer",
        password="Synthetic-Burst-Review-2026!",
        email="burst@example.test",
    )
    config = AppSettings.load()
    config.sub2api_base_url = url
    config.sub2api_admin_token_encrypted = encrypt_secret("Synthetic-Burst-Local-Only")
    config.weekly_quota_model = "constant_average"
    config.safety_factor = Decimal("1")
    config.auto_apply_recommendations = False
    config.save()
    account = create_monitored_account(name="Synthetic burst account")
    people = [
        Participant.objects.create(
            name=name, sub2api_user_id=51 + index, latest_balance_usd=80
        )
        for index, name in enumerate("ABC")
    ]
    for person, share in zip(people, [50, 25, 25]):
        PoolParticipant.objects.create(
            pool=account.pool, participant=person, share_percent=share
        )
    now = timezone.now()
    observation = record(account, people, now, now + timedelta(days=1), [20, 30, 10])
    return account, people, observation


def in_database(fn):
    def execute():
        try:
            return fn()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(execute).result()


def verify(account, people, old):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("BILLING_BROWSER_EXECUTABLE") or None
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(harness.FRONTEND_URL + "/login")
            page.get_by_label("用户名", exact=True).fill("burst-reviewer")
            page.get_by_label("密码", exact=True).fill("Synthetic-Burst-Review-2026!")
            page.get_by_role("button", name="登录", exact=True).click()
            page.wait_for_url(harness.FRONTEND_URL + "/")
            card = page.get_by_role("region", name="临时爽蹬", exact=True)
            expect(card).to_be_visible()
            card.get_by_role("button", name="开启临时爽蹬", exact=True).click()
            dialog = page.locator("dialog[open]").last
            dialog.get_by_role("button", name="取消", exact=True).first.click()
            assert WRITES == []
            card.get_by_role("button", name="开启临时爽蹬", exact=True).click()
            page.get_by_role("button", name="确认开启临时爽蹬", exact=True).click()
            expect(card.get_by_text("本周期生效中", exact=True)).to_be_visible()
            assert WRITES == []
            page.get_by_role(
                "button", name="处理参与者 A 的额度建议", exact=True
            ).click()
            page.locator("dialog[open]").last.get_by_role(
                "button", name=re.compile("一键设置")
            ).click()
            expect(page.locator("dialog[open]")).to_have_count(0)
            assert WRITES == [(51, 9999)]

            def enable_auto():
                config = AppSettings.load()
                config.auto_apply_recommendations = True
                config.save()
                return auto_apply_recommendations()

            assert in_database(enable_auto)["applied"] == 2
            assert all(value == 9999 for value in BALANCES.values())
            page.reload()
            expect(card.get_by_text("本周期生效中", exact=True)).to_be_visible()
            card.screenshot(
                path=str(harness.OUTPUT / "burst-active-desktop.png"),
                animations="disabled",
            )

            def rollover():
                config = AppSettings.load()
                new = record(
                    account,
                    people,
                    old.upstream_resets_at + timedelta(minutes=1),
                    old.upstream_resets_at + timedelta(days=7),
                    [0, 0, 0],
                )
                guard = LeaseGuard.acquire(account.fact_key)
                try:
                    _rebuild_capture(
                        account, SimpleNamespace(plan_type=""), new, config, guard
                    )
                finally:
                    guard.release()
                cycle = TemporaryBurstCycle.objects.get(is_burst_cycle=True)
                assert [
                    Decimal(row["next_adjustment"]) for row in cycle.settlement
                ] == [Decimal("3.33333"), -5, Decimal("1.66667")]
                return auto_apply_recommendations()

            assert in_database(rollover)["applied"] == 3
            assert all(value < 9999 for value in BALANCES.values())
            card.get_by_role("button", name="刷新状态", exact=True).click()
            expect(card.get_by_text("已退出", exact=True)).to_be_visible()
            card.locator("summary").first.click()
            expect(
                card.locator("details").first.get_by_text("+3.33 个百分点", exact=True)
            ).to_be_visible()
            card.screenshot(
                path=str(harness.OUTPUT / "burst-settled-desktop.png"),
                animations="disabled",
            )
            page.set_viewport_size({"width": 390, "height": 844})
            card.screenshot(
                path=str(harness.OUTPUT / "burst-settled-390.png"),
                animations="disabled",
            )
            page.goto(harness.FRONTEND_URL + "/tutorial?page=temporary-burst")
            expect(
                page.get_by_role("heading", name="临时爽蹬", exact=True)
            ).to_be_visible()
            page.get_by_role("button", name="B 超用 5 个百分点", exact=True).click()
            expect(page.get_by_role("cell", name="53.33%", exact=True)).to_be_visible()
            page.screenshot(
                path=str(harness.OUTPUT / "burst-tutorial-390.png"),
                full_page=True,
                animations="disabled",
            )
            page.set_viewport_size({"width": 1440, "height": 1100})
            page.screenshot(
                path=str(harness.OUTPUT / "burst-tutorial-desktop.png"),
                full_page=True,
                animations="disabled",
            )
            assert not errors, errors
            (harness.OUTPUT / "burst-results.json").write_text(
                json.dumps(
                    {
                        "passed": True,
                        "checks": [
                            "cancel causes no writes",
                            "manual 9999",
                            "automatic 9999",
                            "refresh persistence",
                            "live engine rollover",
                            "conserved 3.33333/-5/1.66667 settlement",
                            "automatic ordinary balance restoration",
                            "desktop/mobile card",
                            "illustrated tutorial example",
                        ],
                        "wallet_writes": WRITES,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            page.screenshot(
                path=str(harness.OUTPUT / "burst-failure.png"), full_page=True
            )
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Wallet)
    worker = Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    processes = []
    try:
        seeded = seed(f"http://127.0.0.1:{upstream.server_port}")
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "manage.py",
                    "runserver",
                    f"127.0.0.1:{harness.BACKEND_PORT}",
                    "--noreload",
                ],
                cwd=harness.ROOT / "backend",
                stdout=open(harness.OUTPUT / "django.log", "w"),
                stderr=subprocess.STDOUT,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    "node",
                    str(harness.ROOT / "frontend/node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(harness.FRONTEND_PORT),
                ],
                cwd=harness.ROOT / "frontend",
                stdout=open(harness.OUTPUT / "vite.log", "w"),
                stderr=subprocess.STDOUT,
            )
        )
        harness.wait_for_server(
            f"http://127.0.0.1:{harness.BACKEND_PORT}/api/auth/client-config"
        )
        harness.wait_for_server(harness.FRONTEND_URL + "/login")
        verify(*seeded)
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
