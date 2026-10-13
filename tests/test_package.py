"""Resources must be available from the installed runtime, not a repo root."""
import unittest
from importlib.resources import files


class PackageResourcesTests(unittest.TestCase):
    def test_prompts_and_sre_page_are_packaged(self):
        resources = files('agentic_robot_shop').joinpath('resources')
        for name in ('instructions.txt', 'sre_instructions.txt', 'sre.html'):
            with self.subTest(resource=name):
                self.assertTrue(resources.joinpath(name).read_text(encoding='utf-8').strip())

    def test_sre_page_is_served_without_provider_access(self):
        from fastapi.testclient import TestClient
        from agentic_robot_shop.sre_service import app
        response = TestClient(app).get('/sre')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('raw-data baseline', response.text)
