"""Real MongoDB outage with original catalogue health check; restore replicas."""
import json
import signal
import time
from pathlib import Path
from manage import kube,NS
from verify import request
from chaos import probe
ROOT=Path(__file__).resolve().parent


def main():
    def interrupted(signum,frame):raise RuntimeError('MongoDB outage experiment interrupted')
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,interrupted)
    replicas=json.loads(kube('-n',NS,'get','deployment','mongodb','-o','json'))['spec']['replicas']
    cron=json.loads(kube('-n',NS,'get','cronjob','synthetic-shopping-traffic','-o','json'))['spec'].get('suspend',False)
    evidence={'provider_calls':0,'restored':False,'original_replicas':replicas}
    baseline=request('session',{'profile':'add_known'},service='shopping-load');assert baseline['status']==200
    evidence['baseline']=baseline
    evidence['baseline_health']=request('health',service='catalogue')
    try:
        kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':True}}))
        kube('-n',NS,'scale','deployment/mongodb','--replicas=0')
        kube('-n',NS,'wait','--for=delete','pod','-l','app=mongodb','--timeout=90s')
        evidence['health_without_database']=request('health',service='catalogue')
        evidence['existing_cart_read']=probe('cart',baseline['body']['cart_id'])
        evidence['fault']=request('session',{'profile':'shop'},service='shopping-load')
        assert evidence['fault']['status']==502,evidence['fault']
        assert evidence['existing_cart_read']['ok'],evidence['existing_cart_read']
        evidence['catalogue_deployment']=json.loads(kube('-n',NS,'get','deployment','catalogue','-o','json'))['status']
    finally:
        try:
            kube('-n',NS,'scale','deployment/mongodb','--replicas='+str(replicas))
            kube('-n',NS,'rollout','status','deployment/mongodb','--timeout=180s')
            evidence['restored']=True
        finally:
            kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':cron}}))
            (ROOT/'chaos-mongo-outage.json').write_text(json.dumps(evidence,indent=2)+'\n')
    evidence['recovery_attempts']=[]
    for _ in range(4):
        result=request('session',{'profile':'shop'},service='shopping-load')
        evidence['recovery_attempts'].append(result)
        evidence['recovery']=result
        (ROOT/'chaos-mongo-outage.json').write_text(json.dumps(evidence,indent=2)+'\n')
        if result['status']==200:break
        time.sleep(2)
    assert evidence['recovery']['status']==200,evidence['recovery']
    evidence['native_trace']=request('traces/'+evidence['fault']['body']['trace_id'],service='shopping-load')['body']
    (ROOT/'chaos-mongo-outage.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'restored':True,'provider_calls':0,'health_while_database_absent':evidence['health_without_database'],'catalogue_ready_replicas':evidence['catalogue_deployment'].get('readyReplicas'),'agent_fault':evidence['fault']['body'],'recovery_status':evidence['recovery']['status'],'existing_cart_read_succeeded':evidence['existing_cart_read']['ok']}))

if __name__=='__main__':main()
