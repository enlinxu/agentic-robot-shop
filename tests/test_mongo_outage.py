import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import mongo_outage

class MongoRestorationTests(unittest.TestCase):
    def test_fault_probe_exception_restores_original_replicas_and_traffic(self):
        def kube(*args):
            if 'get' in args:
                return json.dumps({'spec':{'replicas':3}}) if 'deployment' in args else json.dumps({'spec':{'suspend':False}})
            return ''
        count=0
        def request(path,body=None,service=None):
            nonlocal count
            if path=='session':
                count+=1
                if count==2:raise RuntimeError('Probe interrupted')
                return {'status':200,'body':{'cart_id':'disposable-cart'}}
            return {'status':200,'body':{'mongo':True}}
        with patch.object(mongo_outage.signal,'signal'),tempfile.TemporaryDirectory() as folder,patch.object(mongo_outage,'ROOT',Path(folder)),patch.object(mongo_outage,'kube',side_effect=kube) as calls,patch.object(mongo_outage,'request',side_effect=request),patch.object(mongo_outage,'probe',return_value={'ok':True}):
            with self.assertRaisesRegex(RuntimeError,'Probe interrupted'):mongo_outage.main()
            self.assertIn(('-n','agentic-robot-shop','scale','deployment/mongodb','--replicas=3'),[c.args for c in calls.call_args_list])
            self.assertTrue(any(c.args[-1]=='{"spec": {"suspend": false}}' for c in calls.call_args_list))
            evidence=json.loads((Path(folder)/'chaos-mongo-outage.json').read_text())
            self.assertTrue(evidence['restored'])
