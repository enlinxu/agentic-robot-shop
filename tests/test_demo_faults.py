import unittest
from unittest.mock import patch
import demo_faults
from manage import MODES


class DemoFaultTests(unittest.TestCase):
    def test_every_scenario_uses_a_mode_the_mcp_server_accepts(self):
        for scenario in demo_faults.SCENARIOS.values():
            self.assertIn(scenario.mode, MODES)

    def test_mode_is_restored_after_a_normal_run(self):
        with patch('demo_faults.mode') as mode, patch('demo_faults.drive_traffic') as traffic:
            demo_faults.run('contract_violation', 1)
        self.assertEqual([c.args[0] for c in mode.call_args_list], ['schema_mismatch', 'healthy'])
        traffic.assert_called_once_with(30.0, 1)

    def test_mode_is_restored_when_the_run_is_interrupted(self):
        with patch('demo_faults.mode') as mode, patch('demo_faults.drive_traffic', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                demo_faults.run('unhandled_exception', 1)
        self.assertEqual([c.args[0] for c in mode.call_args_list], ['unhandled_exception', 'healthy'])


if __name__ == '__main__':
    unittest.main()
