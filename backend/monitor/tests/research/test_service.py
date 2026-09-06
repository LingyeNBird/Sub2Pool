import base64
import json
from datetime import timedelta
from unittest.mock import Mock
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.utils import timezone
from monitor.models import ResearchSettings
from monitor.research import service, transport
from monitor.research.protocol import STUDY, POLICY, consent_digest
from monitor.tests.helpers import jwt_login
from .synthetic import simulate

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    get_user_model().objects.create_superuser('owner','owner@example.com','very-strong-password')
    client=Client();headers,_=jwt_login(client)
    return client,headers


def patch(admin, **extra):
    client,headers=admin
    body=dict(enabled=True, projects=[STUDY], endpoint='https://study.example.invalid', interval_hours=6, gateway_only=True, accept_consent=True, policy_version=POLICY)
    body.update(extra)
    return client.patch('/api/settings/research',data=json.dumps(body),content_type='application/json',**headers)


def enable(endpoint='https://research.example.org'):
    settings=ResearchSettings.load()
    settings.enabled=True;settings.projects=[STUDY];settings.endpoint=endpoint;settings.gateway_only=True
    settings.consent_hash=consent_digest(endpoint,settings.projects,True);settings.save()
    return settings


def test_defaults_off_and_admin_read_never_sends(admin, monkeypatch):
    send=Mock(side_effect=AssertionError('network not expected'));monkeypatch.setattr(transport,'send',send)
    collect=Mock(side_effect=AssertionError('analysis not expected'));monkeypatch.setattr(service,'collect_cycles',collect)
    client,headers=admin
    response=client.get('/api/settings/research',**headers)
    data=response.json()['data']
    assert not data['enabled'] and not data['consent_current'] and not data['destination_ready']
    assert data['endpoint']=='https://study.example.invalid'
    assert 'identity_encrypted' not in data and 'consent_hash' not in data
    assert service.run_due()=='disabled'
    assert not collect.called and not send.called


def test_consent_destination_scope_and_off_button(admin):
    assert patch(admin,accept_consent=False).status_code==400
    assert patch(admin,policy_version='old').status_code==400
    assert patch(admin).status_code==200
    assert patch(admin,endpoint='https://different.example.org',accept_consent=False).status_code==400
    assert patch(admin,projects=[]).status_code==400
    assert patch(admin,endpoint='https://different.example.org').status_code==200
    client,headers=admin
    assert client.patch('/api/settings/research',data='{"enabled":false}',content_type='application/json',**headers).status_code==200
    assert not ResearchSettings.load().enabled


@pytest.mark.parametrize('endpoint',['http://example.org','https://user:password@example.org','https://example.org/upload','https://example.org/?secret=x','https://example.org/#f','https://example.org:bad'])
def test_invalid_destination_is_validation_error_not_server_error(admin,endpoint):
    assert patch(admin,endpoint=endpoint).status_code==400
    assert not ResearchSettings.load().enabled


def test_unknown_fields_and_generic_settings_cannot_grant_consent(admin):
    assert patch(admin,secret='no').status_code==400
    client,headers=admin
    client.patch('/api/settings',data=json.dumps({'research_enabled':True}),content_type='application/json',**headers)
    assert not ResearchSettings.load().enabled


def test_normal_user_cannot_read_or_write_research(admin):
    get_user_model().objects.create_user('viewer',password='Viewer-Secret-2026!')
    viewer=Client();headers,_=jwt_login(viewer,username='viewer',password='Viewer-Secret-2026!')
    assert viewer.get('/api/settings/research',**headers).status_code==403
    for path in ['settings/research/run','settings/research/withdraw']:
        assert viewer.post('/api/'+path,**headers).status_code==403
    assert Client().get('/api/settings/research').status_code in (401,403)


def test_placeholder_analyzes_without_dns_and_saves_no_new_billing_data(monkeypatch):
    enable('https://study.example.invalid')
    monkeypatch.setattr(service,'collect_cycles',lambda now:(simulate(),{}))
    monkeypatch.setattr(transport.socket,'getaddrinfo',Mock(side_effect=AssertionError('no DNS')))
    assert service.run_due()=='destination_unconfigured'
    settings=ResearchSettings.load()
    assert settings.summary['eligible'] and settings.last_sent_at is None and settings.identity_encrypted==''
    assert service.run_due()=='not_due'


def test_local_report_signature_dedupe_and_revisions(monkeypatch):
    enable();monkeypatch.setattr(service,'collect_cycles',lambda now:(simulate(),{}))
    packets=[]
    def send(endpoint,path,body,signature):
        data=json.loads(body);packets.append(data)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(data['public_key'])).verify(base64.b64decode(signature),b'CodexSubscribeStudy/1\nPOST\n'+path.encode()+b'\n'+body)
        assert set(data)=={'protocol','study_id','method','method_digest','public_key','revision'} | ({'summary'} if path.endswith('reports') else set())
        assert all(x not in body for x in [b'account_id',b'user_id',b'created_at',b'prompt',b'api_key'])
        return {'accepted':True,'revision':data['revision']}
    monkeypatch.setattr(transport,'send',send)
    assert service.run_due()=='sent'
    ResearchSettings.objects.update(next_run_at=None)
    assert service.run_due()=='unchanged'
    assert len(packets)==1
    assert service.withdraw()=='withdrawn'
    assert len(packets)==2 and packets[1]['revision']==2 and 'summary' not in packets[1]
    assert not ResearchSettings.load().enabled


def test_consent_revocation_during_computation_prevents_transmission(monkeypatch):
    enable()
    def collect(now):
        ResearchSettings.objects.update(enabled=False,config_revision=1)
        return simulate(),{}
    monkeypatch.setattr(service,'collect_cycles',collect)
    send=Mock();monkeypatch.setattr(transport,'send',send)
    assert service.run_due()=='consent_changed'
    assert not send.called


def test_failed_analysis_and_uncertain_delivery_are_safe_and_withdrawable(monkeypatch):
    enable()
    def broken(now): raise RuntimeError('sensitive-account-name-and-IP')
    monkeypatch.setattr(service,'collect_cycles',broken)
    assert service.run_due()=='analysis_failed'
    assert 'sensitive' not in ResearchSettings.load().last_error
    ResearchSettings.objects.update(next_run_at=None)
    monkeypatch.setattr(service,'collect_cycles',lambda now:(simulate(),{}))
    monkeypatch.setattr(transport,'send',Mock(side_effect=transport.DeliveryError('科研统计发送失败')))
    assert service.run_due()=='delivery_failed'
    settings=ResearchSettings.load()
    assert settings.last_sent_endpoint and settings.identity_encrypted and settings.last_sent_at is None
    with pytest.raises(transport.DeliveryError): service.withdraw()
    assert ResearchSettings.load().last_status=='withdrawal_failed'
    monkeypatch.setattr(transport,'send',lambda endpoint,path,body,sig:{'accepted':True,'revision':json.loads(body)['revision']})
    assert service.withdraw()=='withdrawn'


def test_method_or_scope_change_invalidates_old_consent(monkeypatch):
    settings=enable();settings.gateway_only=False;settings.save()
    collect=Mock();monkeypatch.setattr(service,'collect_cycles',collect)
    assert service.run_due()=='disabled' and not collect.called


def test_single_flight_and_manual_run_queue(admin,monkeypatch):
    assert patch(admin).status_code==200
    client,headers=admin
    assert client.post('/api/settings/research/run',**headers).status_code==202
    ResearchSettings.objects.update(lease_until=timezone.now()+timedelta(minutes=5))
    assert service.run_due()=='busy'
    assert client.post('/api/settings/research/withdraw',data='{}',content_type='application/json',**headers).status_code==400


def test_identity_is_origin_isolated_and_seed_never_sent():
    settings=enable()
    _,first=transport.identity(settings,'https://one.example.org')
    _,again=transport.identity(settings,'https://one.example.org')
    _,other=transport.identity(settings,'https://two.example.org')
    assert first==again and first!=other
    assert len(base64.b64decode(first))==32


@pytest.mark.parametrize('addresses',[['127.0.0.1'],['10.0.0.1'],['169.254.169.254'],['::1'],['8.8.8.8','192.168.1.1']])
def test_private_or_mixed_dns_is_blocked_before_socket(monkeypatch,addresses):
    monkeypatch.setattr(transport.socket,'getaddrinfo',lambda *args,**kwargs:[(2,1,6,'',(ip,443)) for ip in addresses])
    sock=Mock();monkeypatch.setattr(transport.socket,'create_connection',sock)
    with pytest.raises(transport.DeliveryError): transport.send('https://receiver.example.org','/api/v1/reports',b'{}','test')
    assert not sock.called


def test_no_redirects_or_credentials_or_extra_identifying_headers(monkeypatch):
    captured={}
    class Connection:
        def __init__(self,*args,**kwargs): pass
        def request(self,method,path,body,headers): captured.update(headers)
        def getresponse(self): return Mock(status=307)
        def close(self): pass
    monkeypatch.setattr(transport.socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('8.8.8.8',443))])
    monkeypatch.setattr(transport.socket,'create_connection',lambda *a,**kw:Mock())
    monkeypatch.setattr(transport.ssl,'create_default_context',lambda:Mock(wrap_socket=lambda sock,server_hostname:sock))
    monkeypatch.setattr(transport.http.client,'HTTPConnection',Connection)
    with pytest.raises(transport.DeliveryError,match='307'): transport.send('https://receiver.example.org','/api/v1/reports',b'{}','test')
    assert set(captured)=={'Content-Type','Accept','X-Study-Signature'}


def test_database_import_clears_authorization_but_keeps_withdrawal_key():
    import sqlite3
    import uuid
    from contextlib import closing
    from types import SimpleNamespace
    from django.db import connection
    from monitor.database_transfer import _install_import_guard

    config = enable()
    transport.identity(config, config.endpoint)
    config.config_revision = 4
    config.save()
    with closing(sqlite3.connect(':memory:')) as source:
        source.execute('CREATE TABLE monitor_historymaintenancestate(account_id INTEGER PRIMARY KEY, fact_revision INTEGER, fence_token INTEGER, lease_owner TEXT, lease_expires_at TEXT, updated_at TEXT)')
        with connection.cursor() as cursor:
            cursor.execute("SELECT sql FROM sqlite_master WHERE name='monitor_researchsettings'")
            source.execute(cursor.fetchone()[0])
            cursor.execute('SELECT * FROM monitor_researchsettings')
            rows = cursor.fetchall()
            source.executemany('INSERT INTO monitor_researchsettings VALUES (' + ','.join('?' for _ in cursor.description) + ')', rows)
        guard = SimpleNamespace(account_id=0, token=4, owner=uuid.uuid4(), expires_at=timezone.now()+timedelta(minutes=5))
        _install_import_guard(source, guard)
        value = source.execute('SELECT enabled,consent_hash,config_revision,lease_token,last_status,identity_encrypted FROM monitor_researchsettings').fetchone()
        assert value == (0, '', 5, '', 'disabled', config.identity_encrypted)
