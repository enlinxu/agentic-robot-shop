"""Explicit synthetic model, real agent/MCP/backend execution; zero provider calls.

Run inside a cluster pod with the pinned application dependencies. The model
policy is deterministic load-test input, not evidence of LLM reasoning quality.
"""
import argparse
import asyncio
import json
import os
import time
import uuid
os.environ.setdefault('STATE_DIR','/tmp/shopping-load-evidence')
from pydantic_ai import Agent, models
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart, TextPart
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.usage import UsageLimits
from telemetry import mcp_peer_callback, setup
from opentelemetry import trace

models.ALLOW_MODEL_REQUESTS=False

def policy(cart_id,budget,intent='shop'):
    def response(messages,info):
        returns=[part for message in messages for part in message.parts if isinstance(part,ToolReturnPart)]
        returned={part.tool_name:part.content for part in returns}
        def tool(name,args):return ModelResponse(parts=[ToolCallPart(name,args)])
        if intent in ('add_known','retry_add'):
            if 'add_to_cart' not in returned:return tool('add_to_cart',{'cart_id':cart_id,'sku':'STAN-1','quantity':1})
            if 'get_cart' not in returned:return tool('get_cart',{'cart_id':cart_id})
            return ModelResponse(parts=[TextPart('Synthetic known-item add completed; cart read returned.')])
        if 'search_products' not in returned:return tool('search_products',{'max_price':budget})
        candidates=sorted((p for p in returned['search_products']['products'] if p['instock']>0 and p['price']<=budget),key=lambda p:(p['price'],p['sku']))
        if not candidates:return ModelResponse(parts=[TextPart('No available robot within this budget.')])
        # React to actual detail responses, including stock/price changes since
        # search. Parallel calls must not overwrite each other's tool results.
        inspected={part.content['product']['sku']:part.content['product'] for part in returns if part.tool_name=='get_product'}
        missing=[p for p in candidates[:2] if p['sku'] not in inspected]
        if missing:
            return ModelResponse(parts=[ToolCallPart('get_product',{'sku':p['sku']}) for p in missing])
        available=sorted((inspected[p['sku']] for p in candidates[:2] if inspected[p['sku']]['instock']>0 and inspected[p['sku']]['price']<=budget),key=lambda p:(p['price'],p['sku']))
        if not available:return ModelResponse(parts=[TextPart('None of the inspected robots is now available within budget.')])
        selected=available[0]
        if 'add_to_cart' not in returned:return tool('add_to_cart',{'cart_id':cart_id,'sku':selected['sku'],'quantity':1})
        if 'get_cart' not in returned:return tool('get_cart',{'cart_id':cart_id})
        cart=returned['get_cart']['cart']
        if not any(i['sku']==selected['sku'] and i['qty']==1 for i in cart['items']):
            raise RuntimeError('Actual cart does not contain the requested quantity')
        if cart['total']>budget:
            raise RuntimeError('Actual cart total exceeds the requested budget')
        return ModelResponse(parts=[TextPart('Synthetic shopping session verified actual cart.')])
    return response

async def run_session(index,profile='shop'):
    if profile=='loop':
        controller=lambda messages,info:ModelResponse(parts=[ToolCallPart('get_product',{'sku':'STAN-1'})])
    else:controller=None
    cart_id=str(uuid.uuid4());budget=(70,100,250,1000)[index%4]
    start=time.monotonic()
    with trace.get_tracer('robot-shop.load').start_as_current_span('synthetic_shopping_session',attributes={'test.synthetic':True}) as span:
        toolset=MCPToolset('http://shop-mcp:8080/mcp',process_tool_call=mcp_peer_callback('http://shop-mcp:8080/mcp'),tool_error_behavior='retry' if profile=='retry_add' else 'error',max_retries=1 if profile=='retry_add' else 0,read_timeout=15)
        agent=Agent(FunctionModel(controller or policy(cart_id,budget,profile),model_name='synthetic-'+profile+'-model'),name='synthetic-shopping-agent',toolsets=[toolset],retries=0)
        try:
            result=await asyncio.wait_for(agent.run('Synthetic workload: inspect options, add one within budget, verify cart.',usage_limits=UsageLimits(request_limit=6,tool_calls_limit=8)),timeout=90)
            return {'cart_id':cart_id,'profile':profile,'budget':budget,'synthetic_model':True,'provider_calls':0,'ok':True,'duration_seconds':round(time.monotonic()-start,4),'model_steps':result.usage.requests,'tool_calls':result.usage.tool_calls,'trace_id':format(span.get_span_context().trace_id,'032x')}
        except Exception as exc:
            span.record_exception(exc);span.set_status(trace.StatusCode.ERROR)
            return {'cart_id':cart_id,'profile':profile,'synthetic_model':True,'provider_calls':0,'ok':False,'duration_seconds':round(time.monotonic()-start,4),'error_type':type(exc).__name__,'message':str(exc).split('\n')[0][:250],'trace_id':format(span.get_span_context().trace_id,'032x')}

async def main(args):
    provider=setup('shopping-load')
    Agent.instrument_all(InstrumentationSettings(tracer_provider=provider,include_content=False,include_binary_content=False,include_model_request_parameters=False))
    gate=asyncio.Semaphore(args.concurrency)
    async def bounded(i):
        async with gate:return await run_session(i,args.profile)
    rows=await asyncio.gather(*(bounded(i) for i in range(args.sessions)))
    provider.force_flush()
    print(json.dumps({'synthetic_model':True,'provider_calls':0,'sessions':rows}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--sessions',type=int,default=4);parser.add_argument('--concurrency',type=int,default=2);parser.add_argument('--profile',choices=['shop','loop','add_known','retry_add'],default='shop');args=parser.parse_args()
    if not 1<=args.sessions<=100 or not 1<=args.concurrency<=8:parser.error('sessions1..100, concurrency1..8')
    asyncio.run(main(args))
