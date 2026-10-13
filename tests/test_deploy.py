import unittest
from deploy import options, documents


class DeploymentTests(unittest.TestCase):
    def test_default_has_no_provider_secret_and_traffic_is_suspended(self):
        docs = list(documents(options([])))
        for d in docs:
            if d['kind'] == 'Deployment':
                env = d['spec']['template']['spec']['containers'][0]['env']
                self.assertFalse(any(e['name'] == 'GEMINI_API_KEY' for e in env))
            if d['kind'] == 'CronJob': self.assertTrue(d['spec']['suspend'])

    def test_custom_namespace_registry_storage_and_paid_secret(self):
        docs = list(documents(options(['--namespace', 'shopping', '--registry', 'example.com/demo', '--tag', 'test',
                                       '--paid', '--storage-class', 'fast', '--host', 'shop.example.com'])))
        for d in docs:
            if d['kind'] != 'Namespace': self.assertEqual(d['metadata']['namespace'], 'shopping')
            if d['kind'] == 'PersistentVolumeClaim': self.assertEqual(d['spec']['storageClassName'], 'fast')
            if d['kind'] == 'Deployment' and d['metadata']['name'] == 'shopping-agent':
                c = d['spec']['template']['spec']['containers'][0]
                self.assertEqual(c['image'], 'example.com/demo/agent:test')
                self.assertTrue(any(e['name'] == 'GEMINI_API_KEY' for e in c['env']))

    def test_install_requires_explicit_context(self):
        with self.assertRaises(SystemExit): options(['install'])

    def test_invalid_namespace_rejected(self):
        with self.assertRaises(SystemExit): options(['--namespace', 'bad/namespace'])


if __name__ == '__main__': unittest.main()
