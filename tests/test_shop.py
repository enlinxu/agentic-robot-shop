"""Real SDK validation tests, with only the downstream HTTP service mocked."""
import os
os.environ.setdefault('STATE_DIR','/tmp/ars-unit-evidence')
import unittest
from unittest.mock import AsyncMock, patch
from mcp import Client
from agentic_robot_shop import shop_mcp
PRODUCT={'sku':'STAN-1','name':'Stan','price':67,'instock':1000,'description':'Observability guru'}

class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):shop_mcp.MODE='healthy'

    async def test_healthy_real_sdk(self):
        with patch.object(shop_mcp,'request_json',AsyncMock(return_value=[PRODUCT])):
            async with Client(shop_mcp.server) as client:
                result=await client.call_tool('search_products',{'max_price':100})
        self.assertEqual(result.structured_content['products'][0]['price'],67)

    async def test_fault_real_sdk_and_recovery(self):
        with patch.object(shop_mcp,'request_json',AsyncMock(return_value=[PRODUCT])):
            async with Client(shop_mcp.server) as client:
                shop_mcp.MODE='schema_mismatch'
                with self.assertRaisesRegex(RuntimeError,'Invalid structured content returned by tool search_products'):
                    await client.call_tool('search_products',{'max_price':100})
                shop_mcp.MODE='healthy'
                recovered=await client.call_tool('search_products',{'max_price':100})
                self.assertEqual(recovered.structured_content['products'][0]['price'],67)

    async def test_missing_output_real_sdk_and_recovery(self):
        with patch.object(shop_mcp,'request_json',AsyncMock(return_value=[PRODUCT])):
            async with Client(shop_mcp.server) as client:
                shop_mcp.MODE='missing_output'
                with self.assertRaisesRegex(RuntimeError,'has an output schema but did not return structured content'):
                    await client.call_tool('search_products',{'max_price':100})
                shop_mcp.MODE='healthy'
                recovered=await client.call_tool('search_products',{'max_price':100})
                self.assertEqual(recovered.structured_content['products'][0]['price'],67)

    async def test_unhandled_exception_mode_fails_requests_but_keeps_control_reachable(self):
        import contextlib, io
        from starlette.testclient import TestClient
        client=TestClient(shop_mcp.app)
        self.assertEqual(client.post('/control',json={'mode':'unhandled_exception'}).status_code,200)
        stderr=io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(client.post('/mcp',json={}).status_code,500)
        self.assertIn('RuntimeError: unhandled exception in shop-mcp request /mcp',stderr.getvalue())
        self.assertEqual(client.post('/control',json={'mode':'healthy'}).status_code,200)
        self.assertEqual(client.get('/healthz').json()['mode'],'healthy')

    async def test_cart_backend_called(self):
        backend=AsyncMock(return_value={'items':[{'sku':'STAN-1','qty':1}],'total':67})
        with patch.object(shop_mcp,'request_json',backend):
            data=await shop_mcp.execute('add_to_cart',{'cart_id':'test-cart','sku':'STAN-1','quantity':1})
        backend.assert_awaited_once_with(shop_mcp.CART+'/add/test-cart/STAN-1/1')
        self.assertEqual(data['cart']['items'][0]['sku'],'STAN-1')

    async def test_invalid_quantity_no_backend_write(self):
        backend=AsyncMock()
        with patch.object(shop_mcp,'request_json',backend):
            with self.assertRaises(ValueError):
                await shop_mcp.execute('add_to_cart',{'cart_id':'test-cart','sku':'STAN-1','quantity':0})
        backend.assert_not_awaited()

if __name__=='__main__':unittest.main()
