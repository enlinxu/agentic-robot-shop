"""HTTP/API and persistent budget checks without contacting a model provider."""
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch
os.environ['STATE_DIR']=tempfile.mkdtemp(prefix='ars-agent-tests-')
os.environ['PYDANTIC_AI_NO_BANNER']='1'
from fastapi.testclient import TestClient
from pydantic_ai import Agent as RealAgent
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

class CountedTestModel(TestModel):
    async def count_tokens(self, *args, **kwargs):
        return RequestUsage(input_tokens=1)
import agent

class AgentApiTests(unittest.TestCase):
    def setUp(self):
        agent.DB.execute('DELETE FROM budget');agent.DB.execute('DELETE FROM sessions');agent.DB.commit()
        enabled=patch.dict(os.environ, {'PAID_MODEL_ENABLED':'true'});enabled.start();self.addCleanup(enabled.stop)
        self.client=TestClient(agent.app)
        self.body={'message':'Hello','cart_id':str(uuid.uuid4())}

    def test_invalid_session_does_not_call_model(self):
        with patch.object(agent,'Agent') as factory:
            response=self.client.post('/chat',json={'message':'Hello','cart_id':'not-a-uuid'})
        self.assertEqual(response.status_code,422);factory.assert_not_called()
        self.assertEqual(self.client.get('/healthz').json()['turns_used'],0)

    def test_disabled_paid_chat_never_calls_model(self):
        with patch.dict(os.environ, {'PAID_MODEL_ENABLED':'false'}),patch.object(agent,'Agent') as factory:
            response=self.client.post('/chat',json=self.body)
        self.assertEqual(response.status_code,503);factory.assert_not_called()

    def test_daily_limit_blocks_before_model(self):
        with patch.dict(os.environ,{'DAILY_TURN_LIMIT':'0'}),patch.object(agent,'Agent') as factory:
            response=self.client.post('/chat',json=self.body)
        self.assertEqual(response.status_code,429);factory.assert_not_called()

    def test_real_framework_result_and_persistent_history(self):
        with patch.object(agent,'Agent',side_effect=lambda *a,**k:RealAgent(CountedTestModel())):
            response=self.client.post('/chat',json=self.body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['completed'])
        self.assertEqual(response.json()['usage']['requests'],1)
        self.assertEqual(self.client.get('/healthz').json()['turns_used'],1)
        self.assertIsNotNone(agent.DB.execute('SELECT history FROM sessions WHERE id=?',(self.body['cart_id'],)).fetchone())

    def test_current_provider_model_alias_initializes(self):
        with patch.dict(os.environ,{'GEMINI_API_KEY':'offline-test-key'}):
            actual=RealAgent('google:gemini-2.5-flash-lite')
        self.assertIsNotNone(actual.model)

if __name__=='__main__':unittest.main()

class TraceEvidenceTests(unittest.TestCase):
    def setUp(self):self.client=TestClient(agent.app)

    def test_invalid_trace_never_contacts_remote(self):
        with patch.object(agent.httpx,'AsyncClient') as factory:
            response=self.client.get('/traces/not-a-trace')
        self.assertEqual(response.status_code,400);factory.assert_not_called()

    def test_local_and_remote_native_spans_are_merged(self):
        trace_id='a'*32
        local={'trace_id':trace_id,'span_id':'1','start_ns':2}
        remote={'trace_id':trace_id,'span_id':'2','start_ns':1,'evidence_source':'shop-mcp'}
        actual_client=agent.httpx.AsyncClient
        transport=agent.httpx.MockTransport(lambda request:agent.httpx.Response(200,json=[remote]))
        with patch.object(agent,'read_spans',return_value=[local]),patch.object(agent.httpx,'AsyncClient',side_effect=lambda **kwargs:actual_client(transport=transport)):
            response=self.client.get('/traces/'+trace_id)
        self.assertEqual(response.status_code,200)
        self.assertEqual([s['span_id'] for s in response.json()],['2','1'])
        self.assertEqual(response.json()[1]['evidence_source'],'shopping-agent')

    def test_unavailable_remote_preserves_local_evidence(self):
        trace_id='b'*32
        actual_client=agent.httpx.AsyncClient
        def fail(request):raise agent.httpx.ConnectError('MCP evidence endpoint unavailable')
        transport=agent.httpx.MockTransport(fail)
        with patch.object(agent,'read_spans',return_value=[{'trace_id':trace_id,'span_id':'1','start_ns':1}]),patch.object(agent.httpx,'AsyncClient',side_effect=lambda **kwargs:actual_client(transport=transport)):
            response=self.client.get('/traces/'+trace_id)
        self.assertEqual(response.status_code,200);self.assertEqual(len(response.json()),1)
