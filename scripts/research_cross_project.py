"""Raw facts -> actual worker -> actual local Go receiver -> durable batches.

Only accepts an executable path. The receiver URL is always a newly allocated
loopback port; this script cannot submit to a configured production website.
Run with backend dev dependencies and a built CodexSubscribeStudy executable.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--study-binary',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    data=out/'isolated-data'
    if data.exists(): raise SystemExit('Use a new output directory; no existing database is overwritten')
    module=out/'cross_project_settings.py'
    module.write_text('from pinche.settings import *\nRESEARCH_TEST_ALLOW_LOOPBACK=True\n')
    os.environ.update(DJANGO_SETTINGS_MODULE='cross_project_settings',DJANGO_DEBUG='true',PINCH_DATA_DIR=str(data))
    sys.path[:0]=[str(out),str(root/'backend')]
    import django
    django.setup()
    from django.core.management import call_command
    from django.utils import timezone
    from datetime import timedelta
    from monitor.models import AppSettings,Observation,ResearchSettings,ResearchRequestComponents
    from monitor.models.research import ResearchEvidenceBatch
    from monitor.research import service,transport
    from monitor.research.pooled_protocol import consent_digest,method_digest,STUDY
    from monitor.tests.research.test_pooled_raw import raw_cycle
    from monitor.tests.research.test_data import request
    from monitor.fast_correction.domain import aggregate_fast_logs
    from monitor.fast_correction.rules import FastCorrectionRuleSet
    from monitor.billing_correction.persistence import persist_capture
    call_command('migrate',verbosity=0)
    config=AppSettings.load();config.monitoring_enabled=False;config.save()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    server_log=(out/'receiver.log').open('w')
    proc=subprocess.Popen([str(Path(args.study_binary).resolve())],env={**os.environ,'STUDY_ADDR':f'127.0.0.1:{port}','STUDY_DB':str(data/'receiver.db')},stdout=server_log,stderr=server_log)
    def read():
        with urllib.request.urlopen(base+'/api/v2/studies/gpt6-components',timeout=15) as res:return json.load(res)
    packets=[]
    actual_send=transport.send
    def observed_send(endpoint,path,body,signature):
        assert endpoint==base
        report=json.loads(body)
        summary=report.get('summary',{})
        for key in ('capacity_context','capacity_estimate','particle_filter','constant_average','auxiliary_evidence','auxiliary_groups','account_id','user_id'):
            assert key not in summary and key not in report
        packets.append({'path':path,'revision':report['revision'],'requests':summary.get('requests')})
        return actual_send(endpoint,path,body,signature)
    transport.send=observed_send
    try:
        for _ in range(100):
            try: read();break
            except Exception:time.sleep(.1)
        else:raise AssertionError('Local receiver failed to start')
        setting=ResearchSettings.load();assert not setting.enabled
        assert service.run_due()=='disabled' and packets==[]
        setting.enabled=True;setting.projects=[STUDY];setting.endpoint=base
        setting.consent_hash=consent_digest(base,setting.projects,setting.gateway_only);setting.save()
        rows=raw_cycle(1,modes=[{'service_tier':'priority','long_context_billing_applied':True}])
        assert service.run_due()=='sent'
        result=read();assert result['totals']['requests']==1 and result['totals']['batches']==1
        assert result['causes'][0]['support'] is None
        def due():
            ResearchSettings.objects.filter(pk=1).update(next_run_at=timezone.now())
            return service.run_due()
        assert due()=='unchanged' and len(packets)==1
        previous=rows[-1];end=previous.observed_at+timedelta(minutes=30)
        # Use an earlier fixture so a second increment is inside now.
        row=Observation.objects.create(account_id=7,observed_at=end,window_seconds=604800,
            upstream_resets_at=previous.upstream_resets_at,upstream_used_percent=3,
            total_actual_cost=20,total_standard_cost=20,raw_selected_total_cost=20,selected_total_cost=20,
            effective_usd_per_percent=20,raw_window={'query_mode':'direct'})
        interval=aggregate_fast_logs([request(end-timedelta(minutes=1),id=2000,model='gpt-5.6')],started_at=previous.observed_at,ended_at=end,rules=FastCorrectionRuleSet(config.fast_correction_rules))
        persist_capture(row,interval)
        assert due()=='sent';result=read()
        assert result['totals']['contributors']==1 and result['totals']['requests']==2 and result['totals']['batches']==1
        assert any(c['support'] is not None for c in result['causes'])
        # Arbitrary running estimates and GPT-6 correction do not change bytes.
        Observation.objects.update(effective_usd_per_percent=9999,selected_total_cost=999999,model_diagnostics={'particle_capacity':42})
        config.model_correction_rules=[{'model_pattern':'gpt-6*','multiplier':'99'}];config.save()
        assert due()=='unchanged'
        raw_cycle(1,start=timezone.now()-timedelta(days=400))
        assert due()=='sent';result=read()
        assert result['totals']['requests']==3 and result['totals']['batches']==2
        count=ResearchRequestComponents.objects.count()
        # Closing never withdraws; explicit confirmation is a different action.
        setting=ResearchSettings.load();setting.enabled=False;setting.save()
        assert due()=='disabled' and read()['totals']['requests']==3
        assert service.withdraw()=='withdrawn'
        assert read()['totals']['batches']==0
        assert ResearchRequestComponents.objects.count()==count
        assert ResearchEvidenceBatch.objects.count()==2
        result={'passed':True,'synthetic_only':True,'method_digest':method_digest(),'packets':packets,
            'checks':['default-off','one mixed FAST-long request accepted','same-batch incremental replacement',
                'single-source inference without contributor gate','no estimate inputs','running estimates cannot change report',
                '400-day original history retained','disable does not withdraw','explicit withdrawal preserves local facts']}
        (out/'cross-project-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        transport.send=actual_send
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
        server_log.close()

if __name__=='__main__':main()
