import base64
import unittest
from unittest.mock import patch
import httpx
from trace_evidence import decode_trace, distributed_spans

TRACE='a'*32
SPAN='b'*16

def attr(key,value):return {'key':key,'value':{'stringValue':value}}

def document():
    return {'batches':[{'resource':{'attributes':[attr('service.namespace','agentic-robot-shop'),attr('service.name','cart')]},'scopeSpans':[{'scope':{'name':'redis'},'spans':[{'traceId':base64.b64encode(bytes.fromhex(TRACE)).decode(),'spanId':base64.b64encode(bytes.fromhex(SPAN)).decode(),'parentSpanId':'','name':'redis-GET','kind':'SPAN_KIND_CLIENT','startTimeUnixNano':'10','endTimeUnixNano':'20','status':{'code':'STATUS_CODE_ERROR'},'attributes':[attr('db.system','redis'),attr('db.statement','private cart payload'),attr('gen_ai.input.messages','private prompt')],'events':[{'name':'exception','attributes':[attr('exception.type','RedisError'),attr('exception.message','private data')]}]}]}]}]}

class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_native_ids_status_and_sensitive_attributes(self):
        row=decode_trace(document(),TRACE)[0]
        self.assertEqual(row['span_id'],SPAN)
        self.assertEqual(row['status'],'ERROR')
        self.assertEqual(row['attributes'],{'db.system':'redis'})
        self.assertEqual(row['events'][0]['exception.type'],'RedisError')
        self.assertNotIn('private',str(row))
        self.assertIsNone(row['parent_span_id'])

    def test_other_namespace_and_other_trace_excluded(self):
        d=document();d['batches'][0]['resource']['attributes'][0]=attr('service.namespace','another-app')
        self.assertEqual(decode_trace(d,TRACE),[])
        self.assertEqual(decode_trace(document(),'c'*32),[])

    async def test_deduplicate_and_prefer_local_on_success(self):
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=document())))
        local=dict(decode_trace(document(),TRACE)[0],evidence_source='local')
        with patch('trace_evidence.httpx.AsyncClient',return_value=client):
            self.assertEqual(await distributed_spans(TRACE,[local]),[local])

    async def test_outage_preserves_local_and_invalid_id_rejected(self):
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(503)))
        local=decode_trace(document(),TRACE)[0]
        with patch('trace_evidence.httpx.AsyncClient',return_value=client):
            self.assertEqual(await distributed_spans(TRACE,[local]),[local])
        with self.assertRaises(ValueError):await distributed_spans('../private',[])
