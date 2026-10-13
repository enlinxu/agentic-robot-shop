"""Bounded reversible live fault experiments on the dedicated Robot Shop namespace.

Uses existing upstream GO_SLOW and Kubernetes replicas/CPU quota. Every tool call
uses the real MCP client and actual backends. No provider requests are made.
"""
import argparse
import json
import signal
import subprocess
import uuid
from pathlib import Path
from manage import kube, NS, CONTEXT
ROOT=Path(__file__).resolve().parent

def probe(action,cart_id):
    if not CONTEXT:raise ValueError("Set KUBE_CONTEXT explicitly")
    source=(ROOT/'chaos_probe.py').read_text()
    r=subprocess.run(['kubectl','--context',CONTEXT,'-n',NS,'exec','-i','deployment/shopping-agent','--','python','-',action,'--cart-id',cart_id],input=source,text=True,capture_output=True,check=True,timeout=150)
    return json.loads(r.stdout)

def synthetic(sessions=2,profile='shop',concurrency=2):
    if not CONTEXT:raise ValueError("Set KUBE_CONTEXT explicitly")
    source=(ROOT/'synthetic_load.py').read_text()
    r=subprocess.run(['kubectl','--context',CONTEXT,'-n',NS,'exec','-i','deployment/shopping-agent','--','python','-','--sessions',str(sessions),'--concurrency',str(concurrency),'--profile',profile],input=source,text=True,capture_output=True,check=True,timeout=150)
    return json.loads(r.stdout)

def patch(template):
    kube('-n',NS,'patch','deployment','catalogue','--type=merge','-p',json.dumps(template))
    kube('-n',NS,'rollout','status','deployment/catalogue','--timeout=180s')

def cpu():
    raw=kube('-n',NS,'exec','deployment/catalogue','--','cat','/sys/fs/cgroup/cpu.stat')
    return {line.split()[0]:int(line.split()[1]) for line in raw.splitlines()}

def main(action):
    original=json.loads(kube('-n',NS,'get','deployment','catalogue','-o','json'))['spec']
    container=original['template']['spec']['containers'][0]
    # Store only our public local deployment controls; no secret payloads.
    original_replicas=original['replicas'];original_env=container.get('env',[]);original_resources=container.get('resources',{})
    evidence={'scenario':action,'cart_id':str(uuid.uuid4()),'restored':False}
    cart_id=evidence['cart_id']
    try:
        if action=='concurrent_cart':
            evidence['concurrent_writes']=probe('cart_race',cart_id)
        elif action=='outage':
            evidence['initial_add']=probe('add',cart_id)
            kube('-n',NS,'scale','deployment/catalogue','--replicas=0')
            kube('-n',NS,'wait','--for=delete','pod','-l','app=catalogue','--timeout=90s')
            evidence['search']=probe('search',cart_id)
            evidence['existing_cart_read']=probe('cart',cart_id)
            evidence['cart_add']=probe('add',cart_id)
            evidence['synthetic_agents_during_outage']=synthetic()
        elif action in ('slow_write','retry_write'):
            delayed=[e for e in original_env if e['name']!='GO_SLOW']+[{'name':'GO_SLOW','value':'12000'}]
            patch({'spec':{'template':{'spec':{'containers':[dict(container,env=delayed)]}}}})
            evidence['search']=probe('search',cart_id)
            if action=='retry_write':
                evidence['synthetic_retry']=synthetic(1,'retry_add')
                cart_id=evidence['synthetic_retry']['sessions'][0]['cart_id'];evidence['cart_id']=cart_id
            else:
                evidence['synthetic_add_during_delay']=synthetic(1,'add_known')
                evidence['product']=probe('product',cart_id)
                evidence['add_report']=probe('add',cart_id)
            # Poll the real cart for completion after the upstream deadline; no retry write.
            for _ in range(8):
                evidence['cart_after_reported_failure']=probe('cart',cart_id)
                if sum(i['qty'] for i in evidence['cart_after_reported_failure'].get('data',{}).get('cart',{}).get('items',[])) >= (2 if action=='retry_write' else 1):break
                import time;time.sleep(1)
        elif action=='cpu_pressure':
            evidence['phase']='baseline'
            evidence['baseline']=probe('load',cart_id)
            reduced=dict(original_resources,limits=dict(original_resources.get('limits',{}),cpu='20m'),requests=dict(original_resources.get('requests',{}),cpu='20m'))
            evidence['phase']='quota_rollout'
            patch({'spec':{'template':{'spec':{'containers':[dict(container,resources=reduced)]}}}})
            evidence['phase']='tool_load_under_quota'
            before=cpu();evidence['limited']=probe('load',cart_id);after=cpu()
            evidence['cpu_stat_delta']={k:after[k]-before.get(k,0) for k in after}
            evidence['synthetic_agents_under_cpu_pressure']=synthetic(8,'shop',8)

    except Exception as exc:
        evidence['fault_error_type']=type(exc).__name__
        raise
    finally:
        if action!='concurrent_cart':
            patch({'spec':{'replicas':original_replicas,'template':{'spec':{'containers':[dict(container,env=original_env,resources=original_resources)]}}}})
        evidence['restored']=True
        (ROOT/f'chaos-{action}.json').write_text(json.dumps(evidence,indent=2)+'\n')
    evidence['recovery_search']=probe('search',cart_id)
    if action=='outage':evidence['synthetic_agents_after_recovery']=synthetic()
    if action=='slow_write':
        evidence['retry_add']=probe('add',cart_id)
        evidence['cart_after_retry']=probe('cart',cart_id)
    if action=='cpu_pressure':evidence['recovery']=probe('load',cart_id)
    (ROOT/f'chaos-{action}.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({k:v for k,v in evidence.items() if k not in ('baseline','limited','recovery')}))

if __name__=='__main__':
    def interrupted(signum,frame):raise RuntimeError('Fault experiment interrupted')
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,interrupted)
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['outage','slow_write','retry_write','cpu_pressure','concurrent_cart']);main(parser.parse_args().action)
