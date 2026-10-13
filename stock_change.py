"""Live search/detail consistency test; original inventory is always restored.

This validates simulation fidelity, not real LLM reasoning or a new CML detector.
"""
import json
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from manage import kube,NS
from chaos import patch
from verify import request
ROOT=Path(__file__).resolve().parent


def stock_script(sku,value=None):
    selector=json.dumps({'sku':sku})
    if value is None:return 'print(JSON.stringify(db.getSiblingDB("catalogue").products.findOne('+selector+',{_id:0,sku:1,instock:1})))'
    return 'const r=db.getSiblingDB("catalogue").products.updateOne('+selector+','+json.dumps({'$set':{'instock':value}})+');print(JSON.stringify({modified:r.modifiedCount,changed_at_ms:Date.now()}))'


def stock(sku,value=None):
    return json.loads(kube('-n',NS,'exec','deployment/mongodb','--','mongosh','--quiet','--eval',stock_script(sku,value)))


def changed_between(rows,changed_ns):
    # The MCP server also uses execute_tool. Count the native agent's INTERNAL
    # tools once, rather than double-counting both sides of the transport.
    tools=[s for s in rows if s['kind']=='INTERNAL' and s['attributes'].get('gen_ai.operation.name')=='execute_tool' and s['attributes'].get('gen_ai.agent.name')]
    search=[s for s in tools if s['attributes'].get('gen_ai.tool.name')=='search_products']
    details=[s for s in tools if s['attributes'].get('gen_ai.tool.name')=='get_product']
    return len(search)==1 and search[0]['end_ns']<changed_ns and len(details)==2 and all(s['start_ns']<changed_ns<s['end_ns'] for s in details)


def main():
    def interrupted(signum,frame):raise RuntimeError('Stock consistency test interrupted')
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,interrupted)
    sku='RMC'
    original=stock(sku);assert original['instock']>0
    container=json.loads(kube('-n',NS,'get','deployment','catalogue','-o','json'))['spec']['template']['spec']['containers'][0]
    cron=json.loads(kube('-n',NS,'get','cronjob','synthetic-shopping-traffic','-o','json'))['spec'].get('suspend',False)
    evidence={'provider_calls':0,'original_stock':original,'restored':False}
    try:
        kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':True}}))
        delayed=[e for e in container.get('env',[]) if e['name']!='GO_SLOW']+[{'name':'GO_SLOW','value':'6000'}]
        patch({'spec':{'template':{'spec':{'containers':[dict(container,env=delayed)]}}}})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(request,'session',{'profile':'shop'},'shopping-load')
            time.sleep(.4)
            evidence['stock_change']=stock(sku,0)
            evidence['journey']=future.result(timeout=110)
        assert evidence['journey']['status']==200,evidence['journey']
        result=evidence['journey']['body'];assert result['tool_calls']==5,'Search may have happened after the change'
        evidence['cart']=request('cart/'+result['cart_id'],service='cart')
        assert evidence['cart']['body']['items'][0]['sku']=='STAN-1',evidence['cart']
        assert evidence['cart']['body']['total']==67,evidence['cart']
    finally:
        try:stock(sku,original['instock'])
        finally:
            try:patch({'spec':{'template':{'spec':{'containers':[container]}}}})
            finally:
                kube('-n',NS,'patch','cronjob','synthetic-shopping-traffic','--type=merge','-p',json.dumps({'spec':{'suspend':cron}}))
                (ROOT/'live-stock-change.json').write_text(json.dumps(evidence,indent=2)+'\n')
    evidence['restored']=stock(sku)['instock']==original['instock']
    assert evidence['restored']
    evidence['native_trace']=request('traces/'+result['trace_id'],service='shopping-load')['body']
    changed_ns=evidence['stock_change']['changed_at_ms']*1_000_000
    evidence['change_between_search_and_details']=changed_between(evidence['native_trace'],changed_ns)
    (ROOT/'live-stock-change.json').write_text(json.dumps(evidence,indent=2)+'\n')
    assert evidence['change_between_search_and_details'], 'Native timing did not prove the intended race'
    evidence['recovery']=request('session',{'profile':'shop'},service='shopping-load');assert evidence['recovery']['status']==200
    evidence['recovery_cart']=request('cart/'+evidence['recovery']['body']['cart_id'],service='cart')
    assert evidence['recovery_cart']['body']['items'][0]['sku']=='RMC'
    (ROOT/'live-stock-change.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'restored':True,'provider_calls':0,'changed_between_search_and_details':True,'cart_during_change':evidence['cart']['body'],'recovery_cart':evidence['recovery_cart']['body']}))

if __name__=='__main__':main()
