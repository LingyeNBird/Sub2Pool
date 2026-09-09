"""Exercise the feature through real Django/Vue and a loopback-only synthetic wallet."""

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from socketserver import StreamRequestHandler, ThreadingTCPServer
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
MAILS = []
SMTP_PORT = 0


class Mailbox(StreamRequestHandler):
    """Loopback-only SMTP sink exercising the application's real SMTP transport."""
    def handle(self):
        self.wfile.write(b"220 synthetic.local ESMTP\r\n")
        while line := self.rfile.readline():
            command = line.split(b" ", 1)[0].strip().upper()
            if command == b"DATA":
                self.wfile.write(b"354 End with dot\r\n")
                lines = []
                while (part := self.rfile.readline()) not in (b".\r\n", b""):
                    lines.append(part[1:] if part.startswith(b"..") else part)
                message = BytesParser(policy=policy.default).parsebytes(b"".join(lines))
                assert message["To"] == "admin@example.test"
                MAILS.append(message)
                self.wfile.write(b"250 queued\r\n")
            elif command == b"QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 OK\r\n")


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
            expect(dialog.get_by_role("checkbox")).to_have_count(0)
            expect(dialog.get_by_role("button", name="确认开启临时爽蹬", exact=True)).to_be_enabled()
            dialog.screenshot(path=str(harness.OUTPUT / "burst-carry-confirm.png"))
            dialog.get_by_role("button", name="取消", exact=True).first.click()
            assert WRITES == []
            card.get_by_role("radio", name="不结转：本轮多用不追账", exact=True).check()
            card.get_by_role("button", name="开启临时爽蹬", exact=True).click()
            dialog = page.locator("dialog[open]").last
            expect(dialog.get_by_role("button", name="确认开启临时爽蹬", exact=True)).to_be_disabled()
            dialog.get_by_role("checkbox").check()
            dialog.screenshot(path=str(harness.OUTPUT / "burst-warning-desktop.png"))
            page.get_by_role("button", name="确认开启临时爽蹬", exact=True).click()
            expect(card.get_by_text("本周期生效中", exact=True)).to_be_visible()
            expect(page.get_by_role("complementary", name="爽蹬全局状态")).to_be_visible()
            page.screenshot(path=str(harness.OUTPUT / "burst-atmosphere-desktop.png"), animations="disabled")
            page.goto(harness.FRONTEND_URL + "/tutorial?page=temporary-burst")
            expect(page.get_by_role("complementary", name="爽蹬全局状态")).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(350)
            page.screenshot(path=str(harness.OUTPUT / "burst-atmosphere-390.png"), animations="disabled")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.set_viewport_size({"width": 1440, "height": 1100})
            page.goto(harness.FRONTEND_URL + "/")
            expect(card).to_be_visible()
            reminder = card.get_by_role("switch", name="本轮用满提醒")
            expect(reminder).to_be_disabled()

            def configure_mail():
                config = AppSettings.load()
                config.smtp_host = "127.0.0.1"
                config.smtp_port = SMTP_PORT
                config.smtp_from_email = "sender@example.test"
                config.notification_email = "admin@example.test"
                config.smtp_use_tls = config.smtp_use_ssl = False
                config.notify_on_rate_change = config.notify_on_recommendation_change = False
                config.save()

            in_database(configure_mail)
            card.get_by_role("button", name="刷新状态", exact=True).click()
            expect(reminder).to_be_enabled()
            reminder.click()
            expect(reminder).to_have_attribute("aria-checked", "true")

            def exercise_reminder():
                from monitor.models import NotificationEvent
                from monitor.sampling.notifications import send_observation_notifications
                config = AppSettings.load()
                original = old.upstream_used_percent
                old.upstream_used_percent = Decimal("95")
                send_observation_notifications(config, old, None)
                send_observation_notifications(config, old, None)
                assert len(MAILS) == 1
                NotificationEvent.objects.filter(event_type="temporary_burst_exhaustion").update(
                    created_at=timezone.now() - timedelta(minutes=31)
                )
                send_observation_notifications(config, old, None)
                assert len(MAILS) == 2
                assert "接近用满" in str(MAILS[0]["Subject"])
                assert "不会自动使用重置卡" in MAILS[0].get_body().get_content()
                old.upstream_used_percent = original

            in_database(exercise_reminder)
            page.reload()
            expect(reminder).to_have_attribute("aria-checked", "true")
            reminder.click()
            expect(reminder).to_have_attribute("aria-checked", "false")
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
                for person, share in zip(people, [50, 25, 25]):
                    from monitor.reporting import aggregate_recommendation
                    assert aggregate_recommendation(person, config)[0]["sources"][0]["effective_share_percent"] == share
                assert cycle.settlement_context["eligible"] is False
                return auto_apply_recommendations()

            assert in_database(rollover)["applied"] == 3
            assert all(value < 9999 for value in BALANCES.values())
            card.get_by_role("button", name="刷新状态", exact=True).click()
            expect(card.get_by_text("已退出", exact=True)).to_be_visible()
            card.locator("summary").first.click()
            expect(page.get_by_role("complementary", name="爽蹬全局状态")).to_have_count(0)
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
            example_comic = page.locator("[aria-label='本期消耗与下期权益漫画']")
            next_cycle = example_comic.get_by_role("region", name="下一周期可用权益", exact=True)
            for title, expected in (
                ("整轮用满", ["67.00%", "17.00%", "16.00%"]),
                ("B 超用 5 个百分点", ["53.00%", "20.00%", "27.00%"]),
                ("有人用满，但没人超用", ["50.00%", "25.00%", "25.00%"]),
                ("所有人都没用满", ["50.00%", "25.00%", "25.00%"]),
                ("A 本轮没有使用", ["100.00%", "0.00%", "0.00%"]),
            ):
                page.get_by_role("button", name=title, exact=True).click()
                expect(next_cycle.locator(".actor-value")).to_have_text(expected)
            page.get_by_role("radio", name="不结转模式", exact=True).check()
            expect(next_cycle.locator(".actor-value")).to_have_text(["50.00%", "25.00%", "25.00%"])
            page.get_by_role("radio", name="结转模式", exact=True).check()
            for width in (390, 768, 1440):
                page.set_viewport_size({"width": width, "height": 1100})
                notice_comic = page.locator("[aria-label='重置卡改变车友时间安排的小漫画']")
                notice_comic.screenshot(path=str(harness.OUTPUT / f"notice-comic-{width}.png"), animations="disabled")
                assert notice_comic.evaluate("(element) => element.scrollWidth <= element.clientWidth")
                for title in ("整轮用满", "有人用满，但没人超用"):
                    page.get_by_role("button", name=title, exact=True).click()
                    example_comic.screenshot(path=str(harness.OUTPUT / f"example-comic-{width}-{title}.png"), animations="disabled")
                    assert example_comic.evaluate("(element) => element.scrollWidth <= element.clientWidth")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.set_viewport_size({"width": 390, "height": 844})
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
            comic = page.get_by_role("figure", name="一轮爽蹬，两个结局", exact=True)
            original_theme = page.locator("html").get_attribute("data-theme")
            for width in (390, 768, 1440):
                page.set_viewport_size({"width": width, "height": 1100})
                comic.scroll_into_view_if_needed()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert comic.evaluate("(element) => element.scrollWidth <= element.clientWidth")
                comic.screenshot(
                    path=str(harness.OUTPUT / f"burst-comic-{width}.png"),
                    animations="disabled",
                )
            for theme in ("light", "dark"):
                page.locator("html").evaluate("(element, theme) => element.setAttribute('data-theme', theme)", theme)
                comic.screenshot(
                    path=str(harness.OUTPUT / f"burst-comic-{theme}.png"),
                    animations="disabled",
                )
            page.locator("html").evaluate(
                "(element, theme) => theme === null ? element.removeAttribute('data-theme') : element.setAttribute('data-theme', theme)",
                original_theme,
            )
            def add_second_account():
                second = create_monitored_account(8, pool=account.pool, name="Synthetic second account")
                now = timezone.now()
                record(second, people, now, now + timedelta(days=2), [20, 30, 10])

            in_database(add_second_account)
            page.goto(harness.FRONTEND_URL + "/")
            expect(card).to_be_visible()
            card.get_by_role("radio", name="不结转：本轮多用不追账", exact=True).check()
            card.get_by_role("button", name="开启临时爽蹬", exact=True).click()
            dialog = page.locator("dialog[open]").last
            expect(dialog.get_by_role("button", name="了解共享余额影响，开启爽蹬")).to_be_disabled()
            expect(dialog.get_by_text(re.compile("当前有 2 个账号"))).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            dialog.get_by_role("checkbox").scroll_into_view_if_needed()
            page.screenshot(path=str(harness.OUTPUT / "burst-multi-warning-390.png"))
            dialog.get_by_role("checkbox").check()
            expect(dialog.get_by_role("button", name="了解共享余额影响，开启爽蹬")).to_be_enabled()
            dialog.get_by_role("button", name="取消", exact=True).click()
            def create_current_carry():
                from monitor.temporary_burst import start_session, reconcile_account
                config = AppSettings.load()
                config.weekly_quota_model = "time_varying"
                config.save()
                reset = old.upstream_resets_at + timedelta(days=7)
                record(account, people, reset - timedelta(minutes=1), reset, [33, 33, 34])
                start_session(True)
                next_observation = record(account, people, reset + timedelta(minutes=1),
                                          reset + timedelta(days=7), [0, 0, 0])
                reconcile_account(account, next_observation, config)

            in_database(create_current_carry)
            page.set_viewport_size({"width": 1568, "height": 1000})
            page.goto(harness.FRONTEND_URL + "/allocation")
            carry_input = page.get_by_role("spinbutton", name="Synthetic burst account A 的结转权益百分比", exact=True)
            expect(carry_input).to_have_value("17")
            expect(page.get_by_role("spinbutton", name="Synthetic burst account B 的结转权益百分比", exact=True)).to_have_value("-8")
            expect(page.get_by_role("spinbutton", name="Synthetic second account A 的结转权益百分比", exact=True)).to_have_count(0)
            page.screenshot(path=str(harness.OUTPUT / "carry-allocation-desktop.png"), full_page=True, animations="disabled")
            carry_input.fill("7.5")
            page.get_by_role("button", name="保存分配", exact=True).click()
            expect(carry_input).to_have_value("7.5")
            page.reload()
            expect(carry_input).to_have_value("7.5")
            carry_input.fill("0")
            page.get_by_role("button", name="保存分配", exact=True).click()
            expect(carry_input).to_have_count(0)
            expect(page.get_by_role("spinbutton", name="Synthetic burst account B 的结转权益百分比", exact=True)).to_have_value("-8")
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(350)
            page.screenshot(path=str(harness.OUTPUT / "carry-allocation-390.png"), full_page=True, animations="disabled")
            page.goto(harness.FRONTEND_URL + "/")
            expect(card.get_by_role("button", name="提前终止爽蹬", exact=True)).to_be_visible()
            writes_before_cancel = list(WRITES)
            card.get_by_role("button", name="提前终止爽蹬", exact=True).click()
            stop_dialog = page.locator("dialog[open]").last
            expect(stop_dialog.get_by_role("heading", name="你确定提前终止爽蹬吗？", exact=True)).to_be_visible()
            stop_dialog.locator(".modal-box").screenshot(path=str(harness.OUTPUT / "burst-stop-confirm-390.png"), animations="disabled")
            stop_dialog.get_by_role("button", name="取消", exact=True).click()
            assert WRITES == writes_before_cancel
            page.set_viewport_size({"width": 1440, "height": 1100})
            card.get_by_role("button", name="提前终止爽蹬", exact=True).click()
            stop_dialog.locator(".modal-box").screenshot(path=str(harness.OUTPUT / "burst-stop-confirm-desktop.png"), animations="disabled")
            stop_dialog.get_by_role("button", name="确认提前终止", exact=True).click()
            expect(card.get_by_role("button", name="提前终止爽蹬", exact=True)).to_have_count(0)
            expect(page.get_by_role("complementary", name="爽蹬全局状态")).to_have_count(0)
            card.screenshot(path=str(harness.OUTPUT / "burst-stopped-desktop.png"))
            expect(card.get_by_role("button", name="开启临时爽蹬", exact=True)).to_be_enabled()
            card.get_by_role("radio", name="不结转：本轮多用不追账", exact=True).check()
            card.get_by_role("button", name="开启临时爽蹬", exact=True).click()
            restart_dialog = page.locator("dialog[open]").last
            restart_dialog.get_by_role("checkbox").check()
            restart_dialog.get_by_role("button", name="了解共享余额影响，开启爽蹬", exact=True).click()
            expect(card.get_by_text("本周期生效中", exact=True)).to_be_visible()
            card.screenshot(path=str(harness.OUTPUT / "burst-same-cycle-restarted.png"), animations="disabled")
            card.get_by_role("button", name="提前终止爽蹬", exact=True).click()
            page.locator("dialog[open]").last.get_by_role("button", name="确认提前终止", exact=True).click()
            expect(card.get_by_role("button", name="开启临时爽蹬", exact=True)).to_be_enabled()
            def verify_stopped():
                from monitor.models.temporary_burst import TemporaryBurstSession
                from monitor.temporary_burst import reconcile_account
                session = TemporaryBurstSession.objects.latest("id")
                assert session.terminated_at is not None
                assert not session.exhaustion_reminder_enabled
                assert not session.cycles.filter(settled_at__isnull=True).exists()
                config = AppSettings.load()
                for cycle in list(session.cycles.filter(is_burst_cycle=True)):
                    next_reset = cycle.resets_at + timedelta(days=14)
                    observed = record(cycle.account, people, next_reset - timedelta(days=6), next_reset, [0, 0, 0])
                    reconcile_account(cycle.account, observed, config)
                assert not session.cycles.filter(settled_at__isnull=True).exists()
            in_database(verify_stopped)
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
                            "no-carry mode preserves original next-cycle rights",
                            "notification acknowledgement required only for no-carry mode",
                            "global atmosphere survives navigation and clears on rollover",
                            "automatic ordinary balance restoration",
                            "real SMTP delivery to loopback sink with half-hour throttling",
                            "reminder configuration gate and persisted toggle",
                            "desktop/mobile card",
                            "illustrated tutorial example",
                            "both tutorial modes produce the selected settlement",
                            "red stop confirmation cancel causes no writes",
                            "manual termination restores normal suggestions and cancels all future cycle credits",
                            "editable carry persists and zero hides only its own input",
                            "ordinary account without carry has no extra input",
                        ],
                        "wallet_writes": WRITES,
                        "synthetic_mail_count": len(MAILS),
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
    mail_server = ThreadingTCPServer(("127.0.0.1", 0), Mailbox)
    SMTP_PORT = mail_server.server_address[1]
    mail_worker = Thread(target=mail_server.serve_forever, daemon=True)
    mail_worker.start()
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
        mail_server.shutdown()
        mail_server.server_close()
        mail_worker.join(timeout=5)
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
