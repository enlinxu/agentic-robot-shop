"""Run inside shopping-agent: real MCP requests, no model calls or fabricated spans."""
import argparse
import asyncio
import json
import time
import uuid
from fastmcp import Client
from telemetry import setup, mcp_peer_callback
from opentelemetry import trace
provider=setup('shopping-chaos')

async def call(client, name, args):
    start=time.monotonic()
    with trace.get_tracer('robot-shop.chaos').start_as_current_span('synthetic_tool_probe',attributes={'test.synthetic':True}) as span:
        trace_id=format(span.get_span_context().trace_id,'032x')
        try:
            async def invoke(tool,arguments):return await client.call_tool(tool,arguments,timeout=20)
            result=await mcp_peer_callback('http://shop-mcp:8080/mcp')(None,invoke,name,args)
            return {'tool':name,'trace_id':trace_id,'duration_seconds':round(time.monotonic()-start,4),'ok':True,'data':result.structured_content}
        except Exception as exc:
            span.record_exception(exc);span.set_status(trace.StatusCode.ERROR)
            return {'tool':name,'trace_id':trace_id,'duration_seconds':round(time.monotonic()-start,4),'ok':False,'error_type':type(exc).__name__,'message':str(exc).split('\n')[0][:300]}

async def main(args):
    async with Client('http://shop-mcp:8080/mcp',timeout=20,init_timeout=20) as client:
        if args.action=='search':result=await call(client,'search_products',{'max_price':100})
        elif args.action=='product':result=await call(client,'get_product',{'sku':'STAN-1'})
        elif args.action=='add':result=await call(client,'add_to_cart',{'cart_id':args.cart_id,'sku':'STAN-1','quantity':1})
        elif args.action=='cart':result=await call(client,'get_cart',{'cart_id':args.cart_id})
        elif args.action=='cart_race':
            async def add_session():
                async with Client('http://shop-mcp:8080/mcp',timeout=20,init_timeout=20) as c:
                    return [await call(c,'add_to_cart',{'cart_id':args.cart_id,'sku':'STAN-1','quantity':1}) for _ in range(2)]
            rows=[r for batch in await asyncio.gather(*(add_session() for _ in range(8))) for r in batch]
            cart=await call(client,'get_cart',{'cart_id':args.cart_id})
            reported_successes=sum(r['ok'] for r in rows)
            actual=sum(i['qty'] for i in cart.get('data',{}).get('cart',{}).get('items',[]) if i['sku']=='STAN-1')
            result={'attempted_additions':16,'reported_successes':reported_successes,'actual_quantity':actual,'lost_updates':reported_successes-actual,'cart':cart,'writes':rows}
        elif args.action=='load':
            # Independent sessions model simultaneous consumers of a shared service.
            async def session():
                async with Client('http://shop-mcp:8080/mcp',timeout=20,init_timeout=20) as c:
                    return [await call(c,'search_products',{'max_price':100}) for _ in range(5)]
            start=time.monotonic();rows=[r for batch in await asyncio.gather(*(session() for _ in range(8))) for r in batch]
            durations=sorted(r['duration_seconds'] for r in rows)
            result={'requests':len(rows),'concurrency':8,'wall_seconds':round(time.monotonic()-start,4),'errors':sum(not r['ok'] for r in rows),'p50_seconds':durations[len(rows)//2],'p95_seconds':durations[int(len(rows)*.95)-1],'max_seconds':max(durations),'rows':[{k:v for k,v in r.items() if k!='data'} for r in rows]}
    provider.force_flush()
    print(json.dumps(result))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['search','product','add','cart','load','cart_race']);parser.add_argument('--cart-id',default=str(uuid.uuid4()))
    asyncio.run(main(parser.parse_args()))
