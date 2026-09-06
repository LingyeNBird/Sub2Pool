"""Regression coverage for the PR4 review: imports, signing, and unchanged gates."""
import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone as utc_timezone
from io import BytesIO
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.db import connection
from django.test import override_settings
from django.utils import timezone
import pytest

from monitor.database_transfer import _install_import_guard
from monitor.models import ResearchSettings
from monitor.research import service, transport
from monitor.research.capture import capture_components
from monitor.research.estimator import analyze
from monitor.secrets import encrypt_secret
from monitor.views.research import state
from .synthetic import simulate
from .test_service import admin, enable, patch  # Reuse the existing authenticated fixture.

pytestmark = pytest.mark.django_db


def delivered_settings():
    config = enable()
    _, public = transport.identity(config, config.endpoint)
    config.report_revision = 17
    config.last_sent_at = timezone.now()
    config.last_sent_endpoint = config.endpoint
    config.last_sent_hash = "a" * 64
    config.config_revision = 4
    config.consent_at = timezone.now()
    config.last_computed_at = timezone.now()
    config.summary = {"obsolete_projection": True}
    config.failures = 5
    config.next_run_at = timezone.now()
    config.lease_token = str(uuid.uuid4())
    config.lease_until = timezone.now() + timedelta(minutes=5)
    config.last_status = "sent"
    config.save()
    return config, public


def stage_settings(source):
    """Copy the actual migrated table schemas/data, not a pretend ciphertext."""
    with connection.cursor() as cursor:
        for table in ("monitor_researchsettings", "monitor_historymaintenancestate"):
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=%s", [table])
            source.execute(cursor.fetchone()[0])
        cursor.execute('SELECT * FROM monitor_researchsettings')
        rows = cursor.fetchall()
        source.executemany(
            'INSERT INTO monitor_researchsettings VALUES (' + ','.join('?' for _ in cursor.description) + ')', rows,
        )
    source.execute('CREATE TABLE preserved_fact (original_cost TEXT)')
    source.execute("INSERT INTO preserved_fact VALUES ('100.123456')")
    source.commit()


def import_guard(source):
    guard = SimpleNamespace(account_id=0, token=4, owner=uuid.uuid4(), expires_at=timezone.now() + timedelta(minutes=5))
    _install_import_guard(source, guard)
    assert source.execute('SELECT original_cost FROM preserved_fact').fetchone()[0] == '100.123456'
    source.row_factory = sqlite3.Row
    return dict(source.execute('SELECT * FROM monitor_researchsettings WHERE id=1').fetchone())


def install_settings(row):
    row = dict(row)
    row.pop('id')
    row['projects'] = json.loads(row['projects'])
    row['summary'] = json.loads(row['summary'])
    for field in ResearchSettings._meta.fields:
        value = row.get(field.name)
        if field.get_internal_type() == 'DateTimeField' and isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            row[field.name] = timezone.make_aware(parsed, utc_timezone.utc) if timezone.is_naive(parsed) else parsed
    ResearchSettings.objects.filter(pk=1).update(**row)
    return ResearchSettings.load()


def assert_consent_revoked(row):
    assert row['enabled'] == 0 and row['consent_hash'] == '' and row['consent_at'] is None
    assert row['lease_token'] == '' and row['lease_until'] is None and row['next_run_at'] is None
    assert row['config_revision'] == 5 and row['last_status'] == 'disabled'
    assert json.loads(row['summary']) == {} and row['last_computed_at'] is None and row['failures'] == 0


def verify_packet(path, body, signature):
    packet = json.loads(body)
    Ed25519PublicKey.from_public_bytes(base64.b64decode(packet['public_key'])).verify(
        base64.b64decode(signature), b'CodexSubscribeStudy/1\nPOST\n' + path.encode() + b'\n' + body,
    )
    return packet


def test_import_same_key_preserves_identity_revision_and_explicit_withdrawal(monkeypatch):
    original, public = delivered_settings()
    with closing(sqlite3.connect(':memory:')) as source:
        stage_settings(source)
        row = import_guard(source)
    assert_consent_revoked(row)
    for field in ('identity_encrypted', 'report_revision', 'last_sent_hash', 'last_sent_endpoint'):
        assert row[field] == getattr(original, field)
    assert row['last_sent_at'] is not None
    config = install_settings(row)
    assert state(config)['can_withdraw'] and not state(config)['consent_current']
    calls = []
    def send(endpoint, path, body, signature):
        packet = verify_packet(path, body, signature)
        assert endpoint == original.endpoint and path == '/api/v1/withdraw'
        assert packet['public_key'] == public and packet['revision'] == 18
        calls.append(packet)
        return {'accepted': True, 'revision': packet['revision']}
    monkeypatch.setattr(transport, 'send', send)
    assert service.run_due() == 'disabled'
    assert service.withdraw() == 'withdrawn'
    assert len(calls) == 1


@pytest.mark.parametrize('damage', ['different_key', 'bad_ciphertext', 'non_ascii', 'bad_base64', 'short_seed', 'missing_seed'])
def test_import_unusable_identity_resets_only_local_delivery_state(damage, monkeypatch):
    with override_settings(SECRET_KEY='source-installation-secret-never-shared'):
        config, _ = delivered_settings()
    if damage != 'different_key':
        values = {
            'bad_ciphertext': 'corrupt ciphertext', 'non_ascii': '损坏的密文',
            'bad_base64': encrypt_secret('not base64!'),
            'short_seed': encrypt_secret(base64.b64encode(b'too short').decode()), 'missing_seed': '',
        }
        config.identity_encrypted = values[damage]
        config.save()
    network = Mock(side_effect=AssertionError('import must never send or query DNS'))
    monkeypatch.setattr(transport, 'send', network)
    monkeypatch.setattr(transport.socket, 'getaddrinfo', network)
    with closing(sqlite3.connect(':memory:')) as source:
        stage_settings(source)
        row = import_guard(source)
    assert_consent_revoked(row)
    assert row['identity_encrypted'] == '' and row['report_revision'] == 0
    assert row['last_sent_at'] is None and row['last_sent_hash'] == row['last_sent_endpoint'] == ''
    assert '原实例' in row['last_error'] and '重置' in row['last_error']
    assert row['endpoint'] == config.endpoint and json.loads(row['projects']) == config.projects
    imported = install_settings(row)
    assert not state(imported)['can_withdraw']
    assert service.run_due() == 'disabled' and not network.called


def test_reconsent_after_cross_key_import_can_sign_and_send_again(admin, monkeypatch):
    with override_settings(SECRET_KEY='different-source-installation-key'):
        original, previous_public = delivered_settings()
    with closing(sqlite3.connect(':memory:')) as source:
        stage_settings(source)
        install_settings(import_guard(source))
    assert patch(admin, endpoint=original.endpoint, accept_consent=False).status_code == 400
    assert patch(admin, endpoint=original.endpoint).status_code == 200
    monkeypatch.setattr(service, 'collect_cycles', lambda now: (simulate(), {}))
    calls = []
    def send(endpoint, path, body, signature):
        packet = verify_packet(path, body, signature)
        assert packet['public_key'] != previous_public
        assert packet['revision'] == 1 and endpoint == original.endpoint
        calls.append(packet)
        return {'accepted': True, 'revision': 1}
    monkeypatch.setattr(transport, 'send', send)
    assert service.run_due() == 'sent'
    assert len(calls) == 1 and ResearchSettings.load().identity_encrypted


@pytest.mark.parametrize('value', ['broken-ciphertext', '非ASCII密文', '', 'bad-base64', 'short-seed'])
def test_withdrawal_signing_failure_still_commits_stop_and_keeps_old_identity(admin, monkeypatch, value):
    config, _ = delivered_settings()
    config.lease_token, config.lease_until = '', None
    encrypted = {'bad-base64': encrypt_secret('!'), 'short-seed': encrypt_secret(base64.b64encode(b'bad').decode())}.get(value, value)
    config.identity_encrypted = encrypted
    config.save()
    network = Mock(side_effect=AssertionError('invalid identity must never be sent'))
    monkeypatch.setattr(transport, 'send', network)
    client, headers = admin
    response = client.post('/api/settings/research/withdraw', data='{"confirm":true}', content_type='application/json', **headers)
    assert response.status_code == 502
    config.refresh_from_db()
    assert not config.enabled and config.last_status == 'withdrawal_failed'
    assert config.next_run_at is None and config.lease_token == '' and config.lease_until is None
    assert config.identity_encrypted == encrypted and config.last_sent_endpoint
    assert '科研签名身份' in config.last_error and not network.called


def test_worker_classifies_lost_key_without_silent_identity_rotation(monkeypatch):
    config, _ = delivered_settings()
    config.identity_encrypted = 'unreadable-ciphertext'
    config.lease_token, config.lease_until, config.next_run_at = '', None, None
    config.save()
    monkeypatch.setattr(service, 'collect_cycles', lambda now: (simulate(), {}))
    send = Mock(); monkeypatch.setattr(transport, 'send', send)
    assert service.run_due() == 'delivery_failed'
    config.refresh_from_db()
    assert '科研签名身份' in config.last_error and config.identity_encrypted == 'unreadable-ciphertext'
    assert not send.called


def test_disabled_capture_performs_one_consent_query_only(django_assert_num_queries):
    ResearchSettings.load()
    capture = Mock()
    with django_assert_num_queries(1):
        capture_components(capture, [])
    assert not capture.facts.all.called


def test_ineligible_scores_are_diagnostics_not_support():
    result = analyze(simulate(), gateway_only=False)
    assert not result['eligible'] and result['status'] == 'external_usage_uncontrolled'
    assert any(result['score_mean']) and not any(result['support'])
    insufficient = analyze(simulate()[:1], gateway_only=True)
    assert insufficient['status'] == 'insufficient_data'
    assert not any(insufficient['score_mean']) and not any(insufficient['support'])


def test_real_http_encoder_retains_tls_pinning_and_valid_explicit_host_port(monkeypatch):
    class WireSocket:
        def __init__(self):
            self.sent = b''
            self.closed = False
        def sendall(self, data): self.sent += data
        def makefile(self, mode):
            raw = b'{"accepted":true,"revision":1}'
            return BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(raw)).encode() + b'\r\n\r\n' + raw)
        def close(self): self.closed = True
    sock = WireSocket()
    dial = Mock(return_value=sock)
    tls = Mock(); tls.wrap_socket.return_value = sock
    monkeypatch.setattr(transport.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('8.8.8.8', 443))])
    monkeypatch.setattr(transport.socket, 'create_connection', dial)
    monkeypatch.setattr(transport.ssl, 'create_default_context', lambda: tls)
    result = transport.send('https://receiver.example.org', '/api/v1/reports', b'{}', 'signature')
    assert result['accepted'] is True
    assert b'Host: receiver.example.org:443\r\n' in sock.sent
    dial.assert_called_once_with(('8.8.8.8', 443), timeout=10)
    tls.wrap_socket.assert_called_once_with(sock, server_hostname='receiver.example.org')
    assert sock.closed
