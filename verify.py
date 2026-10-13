"""Bounded real-model E2E: recommendation, real cart write, native fault, recovery."""
import json
import subprocess
import uuid
from pathlib import Path
from manage import kube, mode, NS
ROOT=Path(__file__).resolve().parent

def request(path,body=None,service='shopping-agent'):
    code='''import json,urllib.request,urllib.error
body=BODY
req=urllib.request.Request('http://SERVICE:8080/PATH',data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
try:
 r=urllib.request.urlopen(req,timeout=120)
 status=r.status
except urllib.error.HTTPError as exc:
 r=exc;status=exc.code
print(json.dumps({'status':status,'body':json.load(r)}))
'''.replace('BODY',repr(body)).replace('SERVICE',service).replace('PATH',path)
    return json.loads(kube('-n',NS,'exec','deployment/shopping-agent','--','python','-c',code))

def main():
    evidence={}
    cart=str(uuid.uuid4())
    mode('healthy')
    try:
        evidence['baseline']=request('chat',{'message':'Compare the available in-stock robots priced under 100 EUR. Search the catalogue and recommend one.','cart_id':cart})
        assert evidence['baseline']['status']==200,evidence['baseline']
        evidence['baseline_trace']=request('traces/'+evidence['baseline']['body']['trace_id'])['body']
        assert any(s['attributes'].get('gen_ai.tool.name')=='search_products' or 'search_products' in s['name'] for s in evidence['baseline_trace']), 'no actual tool execution'
        evidence['add']=request('chat',{'message':'Add exactly one Stan robot, SKU STAN-1, to my cart now.','cart_id':cart})
        assert evidence['add']['status']==200,evidence['add']
        evidence['cart']=request('cart/'+cart,service='cart')
        assert evidence['cart']['status']==200,evidence['cart']
        assert any(i['sku']=='STAN-1' and i['qty']==1 for i in evidence['cart']['body']['items']), 'actual shop cart was not changed'
        mode('schema_mismatch')
        evidence['fault']=request('chat',{'message':'Search the catalogue for in-stock robots under 100 EUR and compare them.','cart_id':str(uuid.uuid4())})
        assert evidence['fault']['status']==502,evidence['fault']
        evidence['fault_trace']=request('traces/'+evidence['fault']['body']['trace_id'])['body']
        assert any(s['status']=='ERROR' and any('structured content' in e.get('message_prefix','').lower() for e in s['events']) for s in evidence['fault_trace']), 'native validation exception missing'
    finally:
        mode('healthy')
        (ROOT/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    evidence['recovery']=request('chat',{'message':'Search the catalogue for in-stock robots under 100 EUR and recommend one.','cart_id':str(uuid.uuid4())})
    assert evidence['recovery']['status']==200,evidence['recovery']
    evidence['recovery_trace']=request('traces/'+evidence['recovery']['body']['trace_id'])['body']
    (ROOT/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({phase:evidence[phase]['status'] for phase in ('baseline','add','cart','fault','recovery')}))

if __name__=='__main__':main()
