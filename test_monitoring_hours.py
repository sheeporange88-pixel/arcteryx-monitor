from datetime import datetime
import unittest
from unittest.mock import patch

import monitoring_hours as hours
import monitor_de
import monitor_focused


class MonitoringHoursTests(unittest.TestCase):
    def test_winter_boundaries(self):
        for instant, expected in [
            ('2026-11-18T22:59:59+00:00', True),
            ('2026-11-18T23:00:00+00:00', False),
            ('2026-11-19T05:59:59+00:00', False),
            ('2026-11-19T06:00:00+00:00', True),
        ]:
            with self.subTest(instant=instant):
                self.assertEqual(hours.is_active(datetime.fromisoformat(instant)), expected)

    def test_summer_boundaries(self):
        for instant, expected in [
            ('2027-06-18T21:59:59+00:00', True),
            ('2027-06-18T22:00:00+00:00', False),
            ('2027-06-19T04:59:59+00:00', False),
            ('2027-06-19T05:00:00+00:00', True),
        ]:
            with self.subTest(instant=instant):
                self.assertEqual(hours.is_active(datetime.fromisoformat(instant)), expected)

    def test_daylight_saving_transition_days(self):
        self.assertTrue(hours.is_active(datetime.fromisoformat('2027-03-28T05:00:00+00:00')))
        self.assertFalse(hours.is_active(datetime.fromisoformat('2027-03-28T04:59:59+00:00')))
        self.assertTrue(hours.is_active(datetime.fromisoformat('2027-10-31T06:00:00+00:00')))
        self.assertFalse(hours.is_active(datetime.fromisoformat('2027-10-31T05:59:59+00:00')))

    def test_midnight_blocks_product_and_telegram_requests(self):
        with patch.object(hours, 'is_active', return_value=False), \
             patch.object(monitor_de.urllib.request, 'urlopen') as network:
            for url in ['https://arcteryx.com/de/de/c/mens',
                        'https://outlet.arcteryx.com/de/de/c/mens',
                        'https://api.telegram.org/bot-example/getMe']:
                with self.subTest(url=url), self.assertRaises(hours.QuietHours):
                    monitor_de.request(url)
            network.assert_not_called()

    def test_both_command_lines_skip_during_quiet_hours(self):
        with patch.object(hours, 'is_active', return_value=False), \
             patch.object(monitor_de, 'request') as network:
            with patch('sys.argv', ['monitor_de.py', '--notify']):
                monitor_de.main()
            with patch('sys.argv', ['monitor_focused.py', '--notify']):
                monitor_focused.main()
            network.assert_not_called()
