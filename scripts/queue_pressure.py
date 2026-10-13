"""Measure native request waiting when slow tools exhaust agent concurrency.

Sixteen bounded HTTP sessions, simulated decisions and real backend deadlines.
"""
import json
import signal
import subprocess
from pathlib import Path
from _paths import REPO_ROOT, EVIDENCE_ROOT
from manage import kube,NS,CONTEXT
from chaos import patch
from verify import request
ROOT=EVIDENCE_ROOT


def queue_wait(rows):
    """Native invoke_agent start minus its enclosing HTTP server start.

    This is pre-agent waiting, not a universal proof of a queue. In this app the
    eight-session admission semaphore is the controlled source of that delay.
    Incomplete ancestry or a cross-service chain remains unknown.
    """
    byid={r['span_id']:r for r in rows}
    for agent in rows:
        if agent.get('attributes',{}).get('gen_ai.operation.name')!='invoke_agent':continue
        parent=byid.get(agent.get('parent_span_id'));seen=set()
        for _ in range(64):
            if not parent or parent['span_id'] in seen or parent.get('service_name')!=agent.get('service_name'):break
            seen.add(parent['span_id'])
            attrs=parent.get('attributes',{})
            if parent['kind']=='SERVER' and (attrs.get('http.request.method') or attrs.get('http.method')):
                delta=(agent['start_ns']-parent['start_ns'])/1e9
                return delta if delta>=0 else None
            parent=byid.get(parent.get('parent_span_id'))
    return None


def batch():
    source='''import asyncio,httpx,json,time
async def main():
 async with httpx.AsyncClient(timeout=90) as client:
  async def run(index):
   started=time.monotonic()
   r=await client.post('http://shopping-load:8080/session',json={'profile':'shop'})
   d=r.json();assert d['provider_calls']==0
   return {'index':index,'http_status':r.status_code,'http_seconds':round(time.monotonic()-started,4),'result':d}
  print(json.dumps(await asyncio.gather(*(run(i) for i in range(16)))))
asyncio.run(main())
'''
    if not CONTEXT:raise ValueError("Set KUBE_CONTEXT explicitly")
    return json.loads(subprocess.run(['kubectl','--context',CONTEXT,'-n',NS,'exec','-i','deployment/shopping-agent','--','python','-'],input=source,text=True,capture_output=True,check=True,timeout=150).stdout)


def main():
    def interrupted(signum,frame):raise RuntimeError('Queue experiment interrupted')
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,interrupted)
    container=json.loads(kube('-n',NS,'get','deployment','catalogue','-o','json'))['spec']['template']['spec']['containers'][0]
    cron=json.loads(kube('-n',NS,'get','cronjob','synthetic-shopping-traffic','-o','json'))['spec'].get('suspend',False)
    evidence={'provider_calls':0,'sessions_per_phase':16,'restored':False,'max_running_agent_sessions':8}
    try:
        kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':True}}))
        evidence['baseline']=batch()
        assert all(r['http_status']==200 for r in evidence['baseline'])
        delayed=[e for e in container.get('env',[]) if e['name']!='GO_SLOW']+[{'name':'GO_SLOW','value':'12000'}]
        patch({'spec':{'template':{'spec':{'containers':[dict(container,env=delayed)]}}}})
        evidence['slow_backend']=batch()
    finally:
        try:patch({'spec':{'template':{'spec':{'containers':[container]}}}})
        finally:
            kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':cron}}))
            evidence['restored']=True
            ROOT.mkdir(parents=True, exist_ok=True)
            (ROOT/'chaos-queue-pressure.json').write_text(json.dumps(evidence,indent=2)+'\n')
    evidence['recovery']=batch()
    assert all(r['http_status']==200 for r in evidence['recovery'])
    # Trace export is asynchronous. Fault spans are already ended by recovery.
    chosen=sorted(evidence['slow_backend'],key=lambda r:r['http_seconds'])
    evidence['native_samples']=[]
    for session in (chosen[0],chosen[-1]):
        rows=request('traces/'+session['result']['trace_id'],service='shopping-load')['body']
        evidence['native_samples'].append({'trace_id':session['result']['trace_id'],'pre_agent_wait_seconds':queue_wait(rows),'http_seconds':session['http_seconds'],'agent_seconds':session['result']['duration_seconds'],'spans':rows})
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT/'chaos-queue-pressure.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'restored':True,'provider_calls':0,'phases':{phase:{'http_seconds':sorted(r['http_seconds'] for r in evidence[phase]),'errors':sum(r['http_status']!=200 for r in evidence[phase])} for phase in ('baseline','slow_backend','recovery')},'native_pre_agent_waits':[r['pre_agent_wait_seconds'] for r in evidence['native_samples']]}))

if __name__=='__main__':main()
