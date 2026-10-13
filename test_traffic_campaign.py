import json
import unittest
import httpx
from traffic_campaign import campaign,summarize


class TrafficTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_session_endpoint_and_error_trace_are_preserved(self):
        calls=[]
        def reply(req):
            calls.append(req)
            return httpx.Response(502 if len(calls)==1 else 200,json={
                'provider_calls':0,'synthetic_model':True,'trace_id':'native-trace',
                'tool_calls':5,'error_type':'MCPError'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            result=await campaign(client,3,2,1000)
        self.assertEqual(result['sessions'],3)
        self.assertEqual(result['errors'],1)
        self.assertEqual(result['reported_tool_calls'],15)
        self.assertTrue(all(str(req.url)=='http://shopping-load:8080/session' and json.loads(req.content)=={'profile':'shop'} for req in calls))
        self.assertEqual(result['samples'][0]['trace_id'],'native-trace')

    async def test_missing_cost_confirmation_stops_campaign(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={}))) as client:
            with self.assertRaisesRegex(ValueError,'provider-free'):await campaign(client,1,1,1)

    def test_small_sample_percentiles_and_empty_results(self):
        rows=[{'status':200,'seconds':s} for s in range(1,5)]
        self.assertEqual(summarize(rows,2)['p95_seconds'],4)
        self.assertEqual(summarize(rows,2)['p50_seconds'],2)
        self.assertIsNone(summarize([],0)['p95_seconds'])

    def test_failed_turn_missing_tool_count_is_unknown(self):
        result=summarize([{'status':502,'seconds':8,'tool_calls':None}],8)
        self.assertEqual(result['sessions_without_tool_count'],1)
        self.assertEqual(result['reported_tool_calls'],0)
