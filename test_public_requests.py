import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import monitor_de as common
import notification_policy
import public_requests


class PublicRequestTests(unittest.TestCase):
    def setUp(self):
        self.old = common.PUBLIC_REQUESTS
        common.PUBLIC_REQUESTS = public_requests.PublicRequests()

    def tearDown(self):
        common.PUBLIC_REQUESTS = self.old

    def test_refusal_stops_queued_requests_and_diagnostics_exclude_body_and_cookies(self):
        url = 'https://arcteryx.com/de/de/shop/mens/jacket'
        error = HTTPError(url, 406, 'Not Acceptable',
                          {'Content-Type': 'text/html', 'Set-Cookie': 'PRIVATE'},
                          io.BytesIO(b'<p>Not Acceptable; PRIVATE BODY</p>'))
        with patch.object(common.monitoring_hours, 'ensure_active'), \
             patch.object(common.urllib.request, 'urlopen', side_effect=error) as network:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 406'):
                common.request(url)
            with self.assertRaises(public_requests.SitePaused):
                common.request(url)
            self.assertEqual(network.call_count, 1)
        evidence = json.dumps(common.PUBLIC_REQUESTS.snapshot())
        self.assertNotIn('PRIVATE', evidence)
        self.assertNotIn('set-cookie', evidence)
        self.assertIn('not acceptable', evidence)

    def test_other_site_and_telegram_are_not_blocked(self):
        common.PUBLIC_REQUESTS.blocked['arcteryx.com'] = 406
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'ok'
        with patch.object(common.monitoring_hours, 'ensure_active'), \
             patch.object(common.urllib.request, 'urlopen', return_value=response) as network:
            self.assertEqual(common.request('https://outlet.arcteryx.com/'), 'ok')
            self.assertEqual(common.request('https://api.telegram.org/test'), 'ok')
            self.assertEqual(network.call_count, 2)

    def test_request_starts_are_paced_and_headers_identify_the_monitor(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'ok'
        with patch.object(common.monitoring_hours, 'ensure_active'), \
             patch.object(public_requests.time, 'monotonic', return_value=100), \
             patch.object(public_requests.time, 'sleep') as sleep, \
             patch.object(common.urllib.request, 'urlopen', return_value=response) as network:
            common.request('https://arcteryx.com/de/de/shop/mens/jacket')
            common.request('https://arcteryx.com/de/de/shop/mens/jacket')
            sleep.assert_called_once_with(0.5)
            request = network.call_args.args[0]
            self.assertIn('ArcteryxPriceMonitor', request.get_header('User-agent'))
            self.assertIn('text/html', request.get_header('Accept'))

    def test_retry_after_persists_and_blocks_without_another_request(self):
        controller = public_requests.PublicRequests()
        url = 'https://arcteryx.com/test'
        error = HTTPError(url, 429, 'rate limit', {'Retry-After': '3600'}, io.BytesIO(b''))
        with patch.object(public_requests.time, 'time', return_value=1000):
            controller.failure(url, error)
            new = public_requests.PublicRequests()
            new.restore(controller.cooldowns)
            with self.assertRaisesRegex(public_requests.SitePaused, 'Retry-After'):
                new.before(url)
        self.assertEqual(new.cooldowns['arcteryx.com'], 4600)

    def test_refusal_report_does_not_claim_zero_discounts_or_no_promotion(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(common, 'collect', side_effect=RuntimeError('HTTP 406')), \
                 patch.object(common, 'promotions', side_effect=RuntimeError('HTTP 406')):
                report = common.run(Path(folder), ['main_mens'])
        text = common.summary(report)
        self.assertIn('价格与折扣无法确认', text)
        self.assertIn('首页读取失败', text)
        self.assertNotIn('折扣颜色0个', text)
        self.assertNotIn('未检出明确黑五', text)

    def test_same_access_refusal_is_not_a_new_status_when_worker_order_changes(self):
        a = {'sources': {'main_mens': {'errors': [{'product_id': 'A', 'error': 'HTTP 406'},
                                                       {'product_id': 'B', 'error': '本轮暂停此站后续请求（HTTP 406）'}]}}}
        b = {'sources': {'main_mens': {'errors': [{'product_id': 'B', 'error': 'HTTP 406'},
                                                       {'product_id': 'A', 'error': '本轮暂停此站后续请求（HTTP 406）'}]}}}
        self.assertEqual(notification_policy.report_errors(a), notification_policy.report_errors(b))


if __name__ == '__main__':
    unittest.main()
