"""Kill the real MCP worker during an in-flight write; restore all controls.

Simulated decisions, real services. A transport failure is not rollback evidence.
"""
import json
import time
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from manage import kube, NS
from chaos import patch
from verify import request
ROOT=Path(__file__).resolve().parent

def interrupted(signum,frame):raise RuntimeError('MCP restart experiment interrupted')

def replace_worker(pod):
    # Namespace-init processes may ignore a kill from inside their own namespace.
    # Kubelet terminates the old container; the Deployment creates a fresh pod.
    kube('-n',NS,'delete','pod',pod,'--grace-period=0','--force')


def wait_worker(previous_uid):
    for _ in range(60):
        pods=json.loads(kube('-n',NS,'get','pods','-l','app=shop-mcp','-o','json'))['items']
        for pod in pods:
            if pod['metadata']['uid'] != previous_uid and all(c.get('ready') for c in pod.get('status',{}).get('containerStatuses',[])) and pod.get('status',{}).get('containerStatuses'):
                return pod
        time.sleep(2)
    raise RuntimeError('Replacement MCP worker did not become ready')


def main():
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,interrupted)
    original=json.loads(kube('-n',NS,'get','deployment','catalogue','-o','json'))['spec']
    container=original['template']['spec']['containers'][0]
    cron=json.loads(kube('-n',NS,'get','cronjob','synthetic-shopping-traffic','-o','json'))['spec'].get('suspend',False)
    evidence={'provider_calls':0,'restored':False,'scenario':'mcp_process_restart'}
    baseline=request('session',{'profile':'add_known'},service='shopping-load')
    assert baseline['status']==200
    evidence['baseline']=baseline
    try:
        kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':True}}))
        delayed=[e for e in container.get('env',[]) if e['name']!='GO_SLOW']+[{'name':'GO_SLOW','value':'12000'}]
        patch({'spec':{'template':{'spec':{'containers':[dict(container,env=delayed)]}}}})
        before=json.loads(kube('-n',NS,'get','pods','-l','app=shop-mcp','-o','json'))['items'][0]
        evidence['worker_before']={'pod':before['metadata']['name'],'uid':before['metadata']['uid'],'restart_count':before['status']['containerStatuses'][0]['restartCount']}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(request,'session',{'profile':'add_known'},'shopping-load')
            time.sleep(3)
            assert not future.done(),'Request finished before worker disruption'
            replace_worker(before['metadata']['name'])
            evidence['worker_kill_requested']=True
            evidence['fault']=future.result(timeout=110)
        assert evidence['fault']['status']==502,evidence['fault']
        cart_id=evidence['fault']['body']['cart_id']
        # Cart/catalogue continue independently after the MCP process disappears.
        for _ in range(12):
            evidence['cart_after_agent_failure']=request('cart/'+cart_id,service='cart')
            items=evidence['cart_after_agent_failure'].get('body',{}).get('items',[])
            if items:break
            time.sleep(1)
    finally:
        try:
            patch({'spec':{'template':{'spec':{'containers':[container]}}}})
            replacement=wait_worker(before['metadata']['uid']) if 'before' in locals() else None
            kube('-n',NS,'rollout','status','deployment/shop-mcp','--timeout=180s')
            evidence['restored']=True
        finally:
            kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':cron}}))
            (ROOT/'chaos-mcp-restart.json').write_text(json.dumps(evidence,indent=2)+'\n')
    after=replacement
    assert after['metadata']['uid'] != before['metadata']['uid'],'MCP worker was not replaced'
    evidence['worker_after']={'pod':after['metadata']['name'],'uid':after['metadata']['uid'],'restart_count':after['status']['containerStatuses'][0]['restartCount']}
    evidence['recovery']=request('session',{'profile':'add_known'},service='shopping-load')
    assert evidence['recovery']['status']==200
    trace_id=evidence['fault']['body']['trace_id']
    evidence['native_trace']=request('traces/'+trace_id,service='shopping-load')['body']
    (ROOT/'chaos-mcp-restart.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'restored':True,'provider_calls':0,'fault_status':evidence['fault']['status'],'recovery_status':evidence['recovery']['status'],'worker_before':evidence['worker_before'],'worker_after':evidence['worker_after'],'cart':evidence['cart_after_agent_failure']}))

if __name__=='__main__':main()
