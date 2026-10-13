import unittest
from queue_pressure import queue_wait

class QueueEvidenceTests(unittest.TestCase):
    def test_measure_native_agent_with_enclosing_http_server(self):
        root={'span_id':'root','name':'any route','kind':'SERVER','service_name':'caller','start_ns':1_000_000_000,'attributes':{'http.request.method':'POST'}}
        wrapper={'span_id':'wrapper','parent_span_id':'root','kind':'INTERNAL','service_name':'caller'}
        agent={'span_id':'agent','parent_span_id':'wrapper','name':'any native agent','kind':'INTERNAL','service_name':'caller','attributes':{'gen_ai.operation.name':'invoke_agent'},'start_ns':9_000_000_000}
        self.assertEqual(queue_wait([agent,wrapper,root]),8)
        self.assertIsNone(queue_wait([agent,wrapper]))
        self.assertIsNone(queue_wait([agent,wrapper,dict(root,kind='CLIENT')]))
        self.assertIsNone(queue_wait([agent,dict(wrapper,service_name='different'),root]))
        self.assertIsNone(queue_wait([agent,dict(wrapper,parent_span_id='wrapper'),root]))
        self.assertIsNone(queue_wait([dict(agent,start_ns=0),wrapper,root]))
