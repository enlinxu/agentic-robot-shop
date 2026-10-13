import unittest
from unittest.mock import patch
from mcp_restart import replace_worker, wait_worker

class WorkerDisruptionTests(unittest.TestCase):
    def test_deletion_targets_only_disposable_named_worker(self):
        with patch('mcp_restart.kube') as kube:
            replace_worker('disposable-worker')
            self.assertEqual(kube.call_args.args,('-n','agentic-robot-shop','delete','pod','disposable-worker','--grace-period=0','--force'))

    def test_old_or_unready_worker_does_not_count_as_replacement(self):
        import json
        def pod(uid,ready):return {'metadata':{'uid':uid},'status':{'containerStatuses':[{'ready':ready}]}}
        responses=[{'items':[pod('old',True),pod('new',False)]},{'items':[pod('new',True)]}]
        with patch('mcp_restart.kube',side_effect=[json.dumps(r) for r in responses]),patch('mcp_restart.time.sleep') as sleep:
            self.assertEqual(wait_worker('old')['metadata']['uid'],'new')
            sleep.assert_called_once()
