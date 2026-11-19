from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import server_runner as runner


class ServerRunnerTests(unittest.TestCase):
    def test_fetches_have_no_telegram_credentials_and_delivery_is_separate(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('sys.argv', ['server_runner.py', '--data-root', folder]), \
             patch.object(runner.monitor.monitoring_hours, 'is_active', return_value=True), \
             patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fixture-only'}), \
             patch.object(runner.subprocess, 'run') as run:
            runner.main()
        self.assertEqual(run.call_count, 6)
        for index in (0, 3):
            call = run.call_args_list[index]
            self.assertNotIn('TELEGRAM_BOT_TOKEN', call.kwargs['env'])
            self.assertNotIn('--notify', call.args[0])
        for index in (2, 5):
            self.assertIn('--send-pending', run.call_args_list[index].args[0])

    def test_quiet_hours_make_no_requests_or_subprocesses(self):
        with patch('sys.argv', ['server_runner.py']), \
             patch.object(runner.monitor.monitoring_hours, 'is_active', return_value=False), \
             patch.object(runner, 'preflight') as preflight, \
             patch.object(runner.subprocess, 'run') as run:
            runner.main()
        preflight.assert_not_called()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
