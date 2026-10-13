import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import redis_oom


class RedisRestorationTests(unittest.TestCase):
    def exercise(self, fail_memory_restore):
        commands=[]
        def redis(*args):
            commands.append(args)
            if 'GET' in args:
                return json.dumps({'maxmemory':'0','maxmemory-policy':'allkeys-lru'})
            if args==('CONFIG','SET','maxmemory','0') and fail_memory_restore:
                raise RuntimeError('Memory restoration failed')
            return 'OK'
        def kube(*args):
            commands.append(args)
            return json.dumps({'spec':{'suspend':False}}) if 'get' in args else ''
        with tempfile.TemporaryDirectory() as folder, patch.object(redis_oom,'ROOT',Path(folder)), patch.object(redis_oom.signal,'signal'), patch.object(redis_oom,'redis',side_effect=redis), patch.object(redis_oom,'kube',side_effect=kube), patch.object(redis_oom,'request',return_value={'status':200,'body':{'cart_id':'disposable'}}), patch.object(redis_oom,'probe',side_effect=RuntimeError('Probe interrupted')):
            with self.assertRaisesRegex(RuntimeError,'Memory restoration failed' if fail_memory_restore else 'Probe interrupted'):
                redis_oom.main()
            evidence=json.loads((Path(folder)/'chaos-redis-oom.json').read_text())
        self.assertIn(('CONFIG','SET','maxmemory','0'),commands)
        self.assertIn(('CONFIG','SET','maxmemory-policy','allkeys-lru'),commands)
        self.assertTrue(any(c[-1]=='{"spec": {"suspend": false}}' for c in commands))
        return evidence

    def test_interrupted_probe_restores_both_original_settings(self):
        self.assertTrue(self.exercise(False)['restored'])

    def test_failed_memory_restore_still_restores_policy_and_traffic(self):
        self.assertFalse(self.exercise(True)['restored'])
