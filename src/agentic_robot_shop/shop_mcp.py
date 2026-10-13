"""Real MCP facade over upstream Robot Shop HTTP services."""
import asyncio
import os
from urllib.parse import quote
import httpx
from mcp import types
from mcp.server.lowlevel import Server
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from agentic_robot_shop.telemetry import setup,read_spans
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

setup('shop-mcp')
HTTPXClientInstrumentor().instrument()
MODE = 'healthy'
CATALOGUE = os.getenv('CATALOGUE_URL', 'http://catalogue:8080')
CART = os.getenv('CART_URL', 'http://cart:8080')
PRODUCT = {'type':'object','properties': {'sku':{'type':'string'},'name':{'type':'string'},'price':{'type':'number'},'instock':{'type':'integer'},'description':{'type':'string'}},'required':['sku','name','price','instock'],'additionalProperties':False}
TOOLS = [
 types.Tool(name='search_products', description='Search the real Robot Shop catalogue. Empty query lists all robots. Prices are EUR. Optionally filter by maximum price; returns current stock.', input_schema={'type':'object','properties':{'query':{'type':'string'},'max_price':{'type':'number'}},'additionalProperties':False}, output_schema={'type':'object','properties':{'products':{'type':'array','items':PRODUCT}},'required':['products'],'additionalProperties':False}),
 types.Tool(name='get_product', description='Get a real product by SKU, including price and available stock.',input_schema={'type':'object','properties':{'sku':{'type':'string'}},'required':['sku'],'additionalProperties':False},output_schema={'type':'object','properties':{'product':PRODUCT},'required':['product'],'additionalProperties':False}),
 types.Tool(name='add_to_cart', description='Add a selected robot to the real shopping cart. Only call when the user explicitly asks to add. Use the cart_id supplied by the application.',input_schema={'type':'object','properties':{'cart_id':{'type':'string'},'sku':{'type':'string'},'quantity':{'type':'integer','minimum':1,'maximum':10}},'required':['cart_id','sku','quantity'],'additionalProperties':False},output_schema={'type':'object','properties':{'cart':{'type':'object'}},'required':['cart']}),
 types.Tool(name='get_cart', description='Read the actual shopping cart using the application-supplied cart_id.',input_schema={'type':'object','properties':{'cart_id':{'type':'string'}},'required':['cart_id'],'additionalProperties':False},output_schema={'type':'object','properties':{'cart':{'type':'object'}},'required':['cart']})]

async def request_json(url):
    async with httpx.AsyncClient(timeout=8) as client:
        r=await client.get(url)
        if r.status_code==404 and '/cart/' in url:
            return {'total':0,'tax':0,'items':[]}
        r.raise_for_status()
        return r.json()

def product(p):
    return {k:p[k] for k in ('sku','name','price','instock','description') if k in p}

async def execute(name, args):
    if MODE == 'catalogue_timeout' and name in ('search_products','get_product'):
        await asyncio.sleep(12)
    if name=='search_products':
        q=args.get('query','').strip()
        raw=await request_json(CATALOGUE+('/search/'+quote(q,safe='') if q else '/products'))
        data={'products':[product(p) for p in raw if args.get('max_price') is None or p['price']<=args['max_price']]}
        if MODE=='schema_mismatch' and data['products']:
            # A service contract regression, validated by the real SDK client.
            data['products'][0]['price']=str(data['products'][0]['price'])
        return data
    if name=='get_product':
        return {'product':product(await request_json(CATALOGUE+'/product/'+quote(args['sku'],safe='')))}
    if name=='add_to_cart':
        qty=args['quantity']
        if isinstance(qty,bool) or not isinstance(qty,int) or not 1<=qty<=10:raise ValueError('Quantity must be 1..10')
        return {'cart':await request_json(CART+'/add/'+quote(args['cart_id'],safe='')+'/'+quote(args['sku'],safe='')+'/'+str(qty))}
    if name=='get_cart':
        return {'cart':await request_json(CART+'/cart/'+quote(args['cart_id'],safe=''))}
    raise ValueError('Unknown tool')

async def list_tools(ctx, params):
    return types.ListToolsResult(tools=TOOLS)

async def call_tool(ctx, params):
    data=await execute(params.name, params.arguments or {})
    content=[types.TextContent(text=__import__('json').dumps(data))]
    if MODE=='missing_output' and params.name=='search_products':
        return types.CallToolResult(content=content)
    return types.CallToolResult(content=content,structured_content=data)

async def trace_evidence(request):
    try:rows=read_spans(request.path_params['trace_id'])
    except ValueError:return JSONResponse({'error':'Invalid trace ID'},status_code=400)
    return JSONResponse([dict(row,evidence_source='shop-mcp') for row in rows])

async def health(request):return JSONResponse({'status':'ok','mode':MODE})
async def control(request):
    global MODE
    value=(await request.json()).get('mode')
    if value not in ('healthy','schema_mismatch','missing_output','catalogue_timeout'):return JSONResponse({'error':'invalid mode'},status_code=400)
    MODE=value
    return JSONResponse({'mode':MODE})

server=Server('robot-shop-tools',version='1.0.0',on_list_tools=list_tools,on_call_tool=call_tool)
app=server.streamable_http_app(stateless_http=True,json_response=True,host='0.0.0.0',custom_starlette_routes=[Route('/traces/{trace_id}',trace_evidence),Route('/healthz',health),Route('/control',control,methods=['POST'])])

# Standard ASGI HTTP instrumentation supplies transport spans around SDK spans.
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.util.http import parse_excluded_urls
app=OpenTelemetryMiddleware(app,excluded_urls=parse_excluded_urls("healthz"))
