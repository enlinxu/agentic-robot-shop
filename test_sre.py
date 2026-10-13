import asyncio
import json
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ['STATE_DIR'] = tempfile.mkdtemp(prefix='sre-test-')
import httpx
from fastapi.testclient import TestClient
from sre_raw import METRICS, RawTools, redact
from sre_service import Report, validate_citations, app
from deploy import documents, options
import sre_service


class RawTest(unittest.IsolatedAsyncioTestCase):
    async def test_metric_is_namespaced_and_real_response_preserved(self):
        def respond(request):
            self.assertIn('namespace="agentic-robot-shop"', request.url.params['query'])
            return httpx.Response(200, json={'status': 'success', 'data': {'result': []}})
        raw = RawTools(httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        result = await raw.metrics('cpu')
        self.assertEqual(result['data']['result'], [])
        self.assertIn('unavailable', result['note'])
        with self.assertRaises(ValueError):
            await raw.metrics('up')
        await raw.close()

    async def test_foreign_service_and_bad_trace_rejected_before_network(self):
        raw = RawTools()
        for name in ('foreign-service', '../secrets', 'catalogue/log?token=x'):
            with self.assertRaises(ValueError):
                await raw.logs(name)
        with self.assertRaises(ValueError):
            await raw.trace('../secrets')
        await raw.close()

    async def test_workload_excludes_environment_and_secret_data(self):
        raw = RawTools()
        raw.kubernetes = AsyncMock(side_effect=[{'items': [{'metadata': {'name': 'p', 'labels': {'app': 'cart'}},
              'spec': {'containers': [{'name': 'cart', 'env': [{'name': 'PASSWORD', 'value': 'secret'}]}]}}]},
              {'items': []}, {'items': []}])
        result = await raw.workload()
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('PASSWORD', json.dumps(result))
        await raw.close()

    async def test_upstream_failure_is_not_silently_healthy(self):
        raw = RawTools(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
        with self.assertRaises(httpx.HTTPStatusError):
            await raw.metrics('memory')
        await raw.close()

    async def test_trace_headers_do_not_expand_into_unbounded_search_spans(self):
        data = {'traces': [{'traceID': 'a' * 32, 'rootServiceName': 'shopping-load', 'spanSet': {'spans': ['noise'] * 10000}}]}
        raw = RawTools(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))))
        result = await raw.search_traces()
        self.assertEqual(result['traces'][0]['traceID'], 'a' * 32)
        self.assertLess(len(json.dumps(result)), 500)
        await raw.close()

    async def test_compact_trace_preserves_failure_parentage_and_timing(self):
        row = {'trace_id': 'a' * 32, 'span_id': 'b' * 16, 'parent_span_id': 'c' * 16,
               'service_name': 'shop-mcp', 'name': 'GET', 'status': 'ERROR', 'start_ns': 1000000, 'end_ns': 3000000,
               'events': [{'exception.type': 'ConnectError'}], 'attributes': {'server.address': 'catalogue'}}
        def respond(request):
            return httpx.Response(200, json={'batches': []} if request.url.host == 'tempo' else [row])
        raw = RawTools(httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        result = await raw.trace('a' * 32)
        span = result['spans'][0]
        self.assertEqual(len(result['spans']), 1)
        self.assertEqual(span['parent'], 'c' * 16)
        self.assertEqual(span['duration_ms'], 2)
        self.assertEqual(span['status'], 'ERROR')
        self.assertEqual(span['events'], row['events'])
        await raw.close()

    async def test_failed_ai_run_still_reserves_daily_budget(self):
        sre_service.DB.execute('DELETE FROM runs')
        sre_service.DB.commit()

        async def fail(run):
            run.update(status='failed', error='Simulated worker failure; no provider called')
            sre_service.save(run)
        with patch.dict(os.environ, {'SRE_DAILY_LIMIT': '1', 'PAID_MODEL_ENABLED': 'true'}), patch.object(sre_service, 'investigate', fail):
            run = await sre_service.start_run(sre_service.Investigation(incident='Shopping failed; investigate'))
            await asyncio.gather(*list(sre_service.TASKS))
            self.assertEqual(sre_service.load(run['id'])['status'], 'failed')
            with self.assertRaises(sre_service.HTTPException) as failure:
                await sre_service.start_run(sre_service.Investigation(incident='Shopping failed again'))
            self.assertEqual(failure.exception.status_code, 429)
        sre_service.DB.execute('DELETE FROM runs')
        sre_service.DB.commit()

    async def test_final_model_request_is_reserved_for_report(self):
        for requests, tokens in ((5, 100), (3, 22000)):
            ctx = SimpleNamespace(usage=SimpleNamespace(requests=requests, input_tokens=tokens))
            self.assertIsNone(await sre_service.reserve_report_request(ctx, 'tool'))
        ctx = SimpleNamespace(usage=SimpleNamespace(requests=2, input_tokens=10000))
        self.assertEqual(await sre_service.reserve_report_request(ctx, 'tool'), 'tool')


class BoundaryTest(unittest.TestCase):
    def test_redaction(self):
        text = redact('Bearer abc.def API_KEY=abc password=hunter2 ' + 'AIza' + 'x' * 35)
        for value in ('abc.def', 'hunter2', 'AIza', 'API_KEY=abc'):
            self.assertNotIn(value, text)

    def test_citations_cannot_be_invented_or_cite_failed_tool(self):
        report = Report(conclusion='Unknown', confidence='low', findings=[{'claim': 'x', 'evidence_ids': ['E1']}],
                        alternatives_and_gaps=[], recommended_action='Inspect', verification='Repeat')
        validate_citations(report, [{'id': 'E1', 'status': 'ok'}])
        for evidence in ([], [{'id': 'E1', 'status': 'unavailable'}]):
            with self.assertRaises(ValueError):
                validate_citations(report, evidence)

    def test_deployment_has_only_namespace_scoped_read_permissions(self):
        for doc in documents(options([])):
            if doc['kind'] != 'Namespace':
                self.assertEqual(doc['metadata']['namespace'], 'agentic-robot-shop')
            self.assertNotIn(doc['kind'], ('ClusterRole', 'ClusterRoleBinding', 'Secret'))
            if doc['kind'] == 'Role':
                for rule in doc['rules']:
                    self.assertEqual(rule['verbs'], ['get', 'list'])
                    self.assertNotIn('secrets', rule['resources'])
                    self.assertNotIn('pods/exec', rule['resources'])

    def test_invalid_input_rejected(self):
        client = TestClient(app)
        r = client.post('/sre/api/runs', json={'incident': 'a', 'trace_id': '../secrets'})
        self.assertEqual(r.status_code, 422)


if __name__ == '__main__':
    unittest.main()
