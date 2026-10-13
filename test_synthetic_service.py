"""HTTP errors honestly expose failed synthetic executions to standard OTel."""
import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import synthetic_service

class SyntheticServiceTests(unittest.TestCase):
    def test_scenario_result_controls_actual_http_status(self):
        client=TestClient(synthetic_service.app)
        for ok,code in ((True,200),(False,502)):
            with self.subTest(ok=ok),patch.object(synthetic_service,'run_session',new=AsyncMock(return_value={'ok':ok,'provider_calls':0})) as run:
                response=client.post('/session',json={'profile':'shop'})
                self.assertEqual(response.status_code,code)
                self.assertEqual(response.json()['provider_calls'],0)
                run.assert_awaited_once_with(0,'shop')

    def test_invalid_scenario_is_rejected_before_execution(self):
        with patch.object(synthetic_service,'run_session',new=AsyncMock()) as run:
            response=TestClient(synthetic_service.app).post('/session',json={'profile':'arbitrary'})
            self.assertEqual(response.status_code,422)
            run.assert_not_called()

    def test_invalid_trace_does_not_contact_remote(self):
        with patch.object(synthetic_service.httpx,'AsyncClient') as factory:
            response=TestClient(synthetic_service.app).get('/traces/not-a-trace')
        self.assertEqual(response.status_code,400)
        factory.assert_not_called()

    def test_trace_merges_only_matching_remote_rows(self):
        trace_id='d'*32
        local={'trace_id':trace_id,'span_id':'1','start_ns':2}
        remote={'trace_id':trace_id,'span_id':'2','start_ns':1}
        actual_client=synthetic_service.httpx.AsyncClient
        transport=synthetic_service.httpx.MockTransport(lambda request:synthetic_service.httpx.Response(200,json=[remote,dict(remote,trace_id='e'*32)]))
        with patch.object(synthetic_service,'read_spans',return_value=[local]),patch.object(synthetic_service.httpx,'AsyncClient',side_effect=lambda **kwargs:actual_client(transport=transport)):
            response=TestClient(synthetic_service.app).get('/traces/'+trace_id)
        self.assertEqual([s['span_id'] for s in response.json()],['2','1'])
