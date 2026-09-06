"""Consent -> existing raw facts -> worker -> signed local HTTP -> withdrawal.

Only temporary accounts and quota data; never production endpoints or credentials.
The loopback exception lives solely in a generated test settings module.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal as D

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=Path(os.environ.get('RESEARCH_REVIEW_OUTPUT',ROOT/'research-review-output')).resolve()
OUTPUT.mkdir(parents=True,exist_ok=True)
(OUTPUT/'research_review_settings.py').write_text('from pinche.settings import *\nRESEARCH_TEST_ALLOW_LOOPBACK = True\n')
os.environ.update(DJANGO_SETTINGS_MODULE='research_review_settings',DJANGO_DEBUG='true',PINCH_DATA_DIR=str(OUTPUT/'isolated-database'),PYTHONPATH=str(OUTPUT)+os.pathsep+str(ROOT/'backend'))
sys.path[:0]=[str(OUTPUT),str(ROOT/'backend')]
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import connections
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from monitor.models import AppSettings,ResearchSettings,Observation,ResearchRequestComponents,AnnouncementRead
from monitor.announcements import ANNOUNCEMENTS
from monitor.tests.helpers import create_monitored_account
from monitor.tests.research.synthetic import simulate
from monitor.tests.research.test_data import request
from monitor.research.service import run_due
from monitor.research.protocol import STUDY,method_digest
from monitor.fast_correction.domain import aggregate_fast_logs
from monitor.fast_correction.rules import FastCorrectionRuleSet
from monitor.billing_correction.persistence import persist_capture
from playwright.sync_api import sync_playwright,expect

PACKETS=[]
class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        length=int(self.headers['Content-Length']);assert length<=32768
        body=self.rfile.read(length);data=json.loads(body)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(data['public_key'])).verify(base64.b64decode(self.headers['X-Study-Signature']),b'CodexSubscribeStudy/1\nPOST\n'+self.path.encode()+b'\n'+body)
        assert data['method_digest']==method_digest()
        assert not any(key in body for key in (b'account_id',b'prompt',b'api_key',b'created_at'))
        PACKETS.append({'path':self.path,'revision':data['revision'],'has_summary':'summary' in data})
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps({'accepted':True,'revision':data['revision']}).encode())
    def log_message(self,*args): pass

def sync(fn):
    def wrapped():
        try:return fn()
        finally:connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as worker:return worker.submit(wrapped).result()

def seed():
    call_command('migrate',verbosity=0)
    user,_=get_user_model().objects.get_or_create(username='research-reviewer',defaults={'is_staff':True,'is_superuser':True})
    user.set_password('Synthetic-Local-Research-2026!');user.save()
    for a in ANNOUNCEMENTS:AnnouncementRead.objects.get_or_create(user=user,announcement_code=a.code,defaults={'read_at':timezone.now()})
    config=AppSettings.load();config.monitoring_enabled=False;config.save()
    ResearchSettings.objects.all().delete()
    create_monitored_account()

def seed_raw_facts():
    assert ResearchSettings.load().enabled
    Observation.objects.all().delete()
    counter=0
    for cycle_index,cycle in enumerate(simulate()):
        start=timezone.now().replace(microsecond=0)-timedelta(days=40-cycle_index*8)
        reset=start+timedelta(days=7);pct=0;total=D(0)
        def row(at):
            return Observation.objects.create(account_id=7,observed_at=at,window_seconds=604800,upstream_resets_at=reset,
                upstream_used_percent=pct,total_actual_cost=total,total_standard_cost=total,raw_selected_total_cost=total,selected_total_cost=total,
                effective_usd_per_percent=20,raw_window={'query_mode':'direct'})
        row(start)
        for i,block in enumerate(cycle):
            at=start+timedelta(hours=i+1);counter+=2
            base=D(str(block.baseline));target=tuple(D(str(v)) for v in block.target)
            logs=[request(at-timedelta(minutes=3),id=counter-1,model='gpt-5.6'),request(at-timedelta(minutes=1),id=counter)]
            from dataclasses import replace
            logs=[replace(logs[0],total_cost=base,actual_cost=base,component_costs=(base,D(0),D(0),D(0))),replace(logs[1],total_cost=sum(target),actual_cost=sum(target),component_costs=target)]
            pct+=block.quota;total+=base+sum(target)
            observation=row(at)
            interval=aggregate_fast_logs(logs,started_at=at-timedelta(hours=1),ended_at=at,rules=FastCorrectionRuleSet(AppSettings().fast_correction_rules))
            persist_capture(observation,interval)
    assert ResearchRequestComponents.objects.count()==200

def smoke(endpoint):
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('RESEARCH_BROWSER_EXECUTABLE') or None)
        page=browser.new_page(viewport={'width':1440,'height':1000});page.set_default_timeout(15000)
        page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            page.goto('http://127.0.0.1:5173/login');page.get_by_label('用户名',exact=True).fill('research-reviewer');page.get_by_label('密码',exact=True).fill('Synthetic-Local-Research-2026!')
            page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url('http://127.0.0.1:5173/')
            page.goto('http://127.0.0.1:5173/settings')
            card=page.locator('section').filter(has=page.get_by_role('heading',name='科研共创',exact=True))
            expect(card.get_by_text('未开启',exact=True)).to_be_visible()
            assert not sync(lambda:ResearchSettings.load().enabled) and PACKETS==[]
            card.get_by_role('checkbox',name='开启科研共创（保存并确认后生效）').check()
            card.get_by_role('checkbox',name='GPT-6 额度异常归因',exact=True).check()
            card.get_by_role('checkbox',name='我确认研究涉及的账号',exact=False).check()
            card.get_by_role('button',name='保存科研设置',exact=True).click()
            dialog=page.locator('dialog[open]')
            expect(dialog.get_by_role('heading',name='确认科研共创授权')).to_be_visible()
            expect(dialog.get_by_text('https://study.example.invalid',exact=True)).to_be_visible()
            expect(dialog.get_by_role('button',name='同意并开启')).to_be_disabled()
            dialog.get_by_role('button',name='取消',exact=True).click()
            assert not sync(lambda:ResearchSettings.load().enabled)
            card.get_by_role('button',name='保存科研设置',exact=True).click()
            page.screenshot(path=str(OUTPUT/'research-consent-desktop.png'),full_page=True)
            dialog.get_by_role('checkbox').check();dialog.get_by_role('button',name='同意并开启').click()
            expect(page.locator('dialog[open]')).to_have_count(0)
            sync(seed_raw_facts)
            assert sync(run_due)=='destination_unconfigured' and PACKETS==[]
            page.reload();expect(card.get_by_text('仅本地分析 · 接收网站待配置',exact=True)).to_be_visible()
            expect(card.get_by_text('本安装 · 滚动 90 天',exact=True)).to_be_visible()
            card.screenshot(path=str(OUTPUT/'research-local-results.png'))
            # Changing the destination requires a NEW explicit consent. The
            # generated test settings alone allow a local non-TLS mock receiver.
            card.get_by_label('科研接收网站',exact=True).fill(endpoint)
            card.get_by_role('button',name='保存科研设置',exact=True).click()
            expect(dialog.get_by_text(endpoint,exact=True)).to_be_visible()
            page.set_viewport_size({'width':390,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(OUTPUT/'research-consent-mobile.png'),full_page=True)
            dialog.get_by_role('checkbox').check();dialog.get_by_role('button',name='同意并开启').click()
            expect(page.locator('dialog[open]')).to_have_count(0)
            assert sync(run_due)=='sent'
            assert len(PACKETS)==1 and PACKETS[0]['has_summary']
            page.reload();expect(card.get_by_text('统计已发送',exact=True)).to_be_visible()
            card.get_by_role('button',name='立即停止分享',exact=True).click()
            expect(card.get_by_text('未开启',exact=True)).to_be_visible()
            assert sync(run_due)=='disabled' and len(PACKETS)==1
            card.get_by_role('button',name='停止并撤回已提交统计',exact=True).click()
            dialog.get_by_role('button',name='确认撤回',exact=True).click()
            expect(page.locator('dialog[open]')).to_have_count(0)
            expect(card.get_by_text('统计已撤回',exact=True)).to_be_visible()
            assert len(PACKETS)==2 and PACKETS[1]['path']=='/api/v1/withdraw' and not PACKETS[1]['has_summary']
            assert PACKETS[1]['revision']>PACKETS[0]['revision']
            assert not errors,errors
            (OUTPUT/'browser-results.json').write_text(json.dumps({'synthetic_only':True,'passed':True,'page_errors':errors,'packets':PACKETS,
                'checks':['off by default','cancel is not consent','privacy destination notice','placeholder sends nothing','real original components','independent worker computation','new origin reconsent','signed HTTP aggregate only','mobile consent','immediate stop','signed withdrawal']},indent=2)+'\n')
        except Exception:
            (OUTPUT/'failure.txt').write_text(traceback.format_exc());page.screenshot(path=str(OUTPUT/'failure.png'),full_page=True);raise
        finally:browser.close()

if __name__=='__main__':
    seed();receiver=ThreadingHTTPServer(('127.0.0.1',0),Receiver);thread=Thread(target=receiver.serve_forever,daemon=True);thread.start()
    processes=[]
    try:
        processes.append(subprocess.Popen([sys.executable,'manage.py','runserver','127.0.0.1:8000','--noreload'],cwd=ROOT/'backend',stdout=open(OUTPUT/'django.log','w'),stderr=subprocess.STDOUT))
        processes.append(subprocess.Popen(['node_modules/.bin/vite','--host','127.0.0.1'],cwd=ROOT/'frontend',stdout=open(OUTPUT/'vite.log','w'),stderr=subprocess.STDOUT))
        for url in ['http://127.0.0.1:8000/api/auth/client-config','http://127.0.0.1:5173/login']:
            for _ in range(100):
                try:urllib.request.urlopen(url,timeout=1).close();break
                except Exception:time.sleep(.2)
            else:raise RuntimeError('Local test server not ready')
        smoke(f'http://127.0.0.1:{receiver.server_port}')
    finally:
        receiver.shutdown();receiver.server_close();thread.join(timeout=5)
        for proc in processes:proc.terminate()
        for proc in processes:
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
