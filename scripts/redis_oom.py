"""Actual Redis write refusal without eviction; restore configuration in finally.

Run only after other fault experiments/recovery finish. Existing data is retained.
Simulated model executes actual add_to_cart; no provider request is possible.
"""
import json
import time
import signal
from pathlib import Path
from _paths import REPO_ROOT, EVIDENCE_ROOT
from manage import kube,NS
from verify import request
from chaos import probe
ROOT=EVIDENCE_ROOT

def redis(*args):return kube('-n',NS,'exec','deployment/redis','--','redis-cli',*args)
def interrupted(signum,frame):raise RuntimeError('Redis pressure test interrupted')

def main():
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,interrupted)
    raw=json.loads(redis('--json','CONFIG','GET','maxmemory','maxmemory-policy'))
    original=raw if isinstance(raw,dict) else dict(zip(raw[::2],raw[1::2]))
    cron=json.loads(kube('-n',NS,'get','cronjob','synthetic-shopping-traffic','-o','json'))['spec'].get('suspend',False)
    evidence={'provider_calls':0,'restored':False,'original':original}
    baseline=request('session',{'profile':'add_known'},service='shopping-load')
    assert baseline['status']==200,baseline
    evidence['baseline']=baseline
    try:
        kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':True}}))
        # noeviction rejects new writes while retaining all existing cart keys.
        assert redis('CONFIG','SET','maxmemory-policy','noeviction').strip()=='OK'
        assert redis('CONFIG','SET','maxmemory','1').strip()=='OK'
        evidence['existing_cart_under_pressure']=probe('cart',baseline['body']['cart_id'])
        evidence['agent_write_under_pressure']=request('session',{'profile':'add_known'},service='shopping-load')
        trace_id=evidence['agent_write_under_pressure']['body']['trace_id']
        evidence['native_trace']=request('traces/'+trace_id,service='shopping-load')['body']
        evidence['redis_error_stats']=redis('INFO','errorstats')
        assert evidence['agent_write_under_pressure']['status']==502,evidence['agent_write_under_pressure']
        assert evidence['existing_cart_under_pressure']['ok'],evidence['existing_cart_under_pressure']
    finally:
        memory_restored=False
        try:
            assert redis('CONFIG','SET','maxmemory',str(original['maxmemory'])).strip()=='OK'
            memory_restored=True
        finally:
            try:
                assert redis('CONFIG','SET','maxmemory-policy',str(original['maxmemory-policy'])).strip()=='OK'
                evidence['restored']=memory_restored
            finally:
                kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':cron}}))
                ROOT.mkdir(parents=True, exist_ok=True)
                (ROOT/'chaos-redis-oom.json').write_text(json.dumps(evidence,indent=2)+'\n')
    # Collector batching is asynchronous; query after restoring Redis.
    for _ in range(10):
        rows=request('traces/'+trace_id,service='shopping-load')['body']
        evidence['native_trace']=rows
        if any(row.get('service_name')=='cart' and row['attributes'].get('db.system',row['attributes'].get('db.system.name'))=='redis' for row in rows):
            break
        time.sleep(2)
    evidence['recovery']=request('session',{'profile':'add_known'},service='shopping-load')
    assert evidence['recovery']['status']==200,evidence['recovery']
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT/'chaos-redis-oom.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'restored':True,'provider_calls':0,'status_sequence':[baseline['status'],evidence['agent_write_under_pressure']['status'],evidence['recovery']['status']]}))

if __name__=='__main__':main()
