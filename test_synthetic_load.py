"""Synthetic policy runs through the real agent and MCP SDK, without a provider."""
import os
os.environ.setdefault('STATE_DIR','/tmp/ars-synthetic-tests')
import unittest
from unittest.mock import AsyncMock,patch
from pydantic_ai import Agent
from pydantic_ai import FunctionToolset
from mcp import Client as SDKClient
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits
import shop_mcp
from synthetic_load import policy

def sdk_toolset(client):
    async def invoke(name,args):return (await client.call_tool(name,args)).structured_content
    async def search_products(max_price:float):return await invoke('search_products',{'max_price':max_price})
    async def get_product(sku:str):return await invoke('get_product',{'sku':sku})
    async def add_to_cart(cart_id:str,sku:str,quantity:int):return await invoke('add_to_cart',{'cart_id':cart_id,'sku':sku,'quantity':quantity})
    async def get_cart(cart_id:str):return await invoke('get_cart',{'cart_id':cart_id})
    return FunctionToolset(tools=[search_products,get_product,add_to_cart,get_cart])

class SyntheticJourneyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):shop_mcp.MODE='healthy'

    async def test_multiple_tools_verify_actual_backend_state(self):
        products=[{'sku':'A','name':'A','price':42,'instock':5},{'sku':'B','name':'B','price':67,'instock':2},{'sku':'C','name':'C','price':10,'instock':0}]
        state={'items':[],'total':0}
        async def backend(url):
            if url.endswith('/products'):return products
            if '/product/' in url:return next(p for p in products if p['sku']==url.rsplit('/',1)[1])
            if '/add/' in url:
                state['items']=[{'sku':'A','qty':1}];state['total']=42
                return state
            if '/cart/' in url:return state
            raise AssertionError(url)
        mocked=AsyncMock(side_effect=backend)
        with patch.object(shop_mcp,'request_json',mocked):
            async with SDKClient(shop_mcp.server) as client:
                agent=Agent(FunctionModel(policy('test-cart',100)),toolsets=[sdk_toolset(client)])
                result=await agent.run('Synthetic test',usage_limits=UsageLimits(request_limit=6,tool_calls_limit=8))
        self.assertIn('verified',result.output)
        self.assertEqual(result.usage.tool_calls,5)
        urls=[c.args[0] for c in mocked.await_args_list]
        self.assertIn(shop_mcp.CART+'/add/test-cart/A/1',urls)
        self.assertNotIn(shop_mcp.CATALOGUE+'/product/C',urls)
        self.assertEqual(state['items'],[{'sku':'A','qty':1}])

    async def test_schema_failure_stops_before_cart_write(self):
        shop_mcp.MODE='schema_mismatch'
        backend=AsyncMock(return_value=[{'sku':'A','name':'A','price':42,'instock':1}])
        with patch.object(shop_mcp,'request_json',backend):
            async with SDKClient(shop_mcp.server) as client:
                agent=Agent(FunctionModel(policy('test-cart',100)),toolsets=[sdk_toolset(client)])
                with self.assertRaises(RuntimeError):await agent.run('Synthetic test')
        self.assertEqual(backend.await_count,1)
        self.assertTrue(backend.await_args.args[0].endswith('/products'))

    async def test_actual_detail_stock_change_selects_other_robot(self):
        products=[{'sku':'A','name':'A','price':42,'instock':5},{'sku':'B','name':'B','price':67,'instock':2}]
        state={'items':[],'total':0}
        async def backend(url):
            if url.endswith('/products'):return products
            if '/product/' in url:
                product=next(p for p in products if p['sku']==url.rsplit('/',1)[1])
                return dict(product,instock=0) if product['sku']=='A' else dict(product,price=65)
            if '/add/' in url:
                self.assertTrue(url.endswith('/B/1'))
                state.update(items=[{'sku':'B','qty':1}],total=65);return state
            if '/cart/' in url:return state
            raise AssertionError(url)
        with patch.object(shop_mcp,'request_json',AsyncMock(side_effect=backend)):
            async with SDKClient(shop_mcp.server) as client:
                result=await Agent(FunctionModel(policy('fresh-cart',70)),toolsets=[sdk_toolset(client)]).run('Synthetic test')
        self.assertIn('verified',result.output)
        self.assertEqual(result.usage.tool_calls,5)
        self.assertEqual(state['items'],[{'sku':'B','qty':1}])

    async def test_all_inspected_stock_changed_stops_before_write(self):
        product={'sku':'A','name':'A','price':42,'instock':5}
        async def backend(url):
            if url.endswith('/products'):return [product]
            if '/product/' in url:return dict(product,instock=0)
            raise AssertionError('Unexpected cart write or read')
        with patch.object(shop_mcp,'request_json',AsyncMock(side_effect=backend)):
            async with SDKClient(shop_mcp.server) as client:
                result=await Agent(FunctionModel(policy('fresh-cart',70)),toolsets=[sdk_toolset(client)]).run('Synthetic test')
        self.assertIn('None of the inspected',result.output)
        self.assertEqual(result.usage.tool_calls,2)

    async def test_provider_requests_are_blocked_before_sdk_call(self):
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider
        model=GoogleModel('gemini-2.5-flash-lite',provider=GoogleProvider(api_key='synthetic-test-never-send'))
        with patch.object(model,'_generate_content',new=AsyncMock()) as provider_call:
            with self.assertRaisesRegex(RuntimeError,'Model requests are not allowed'):
                await Agent(model).run('This must never reach a paid provider')
            provider_call.assert_not_awaited()
