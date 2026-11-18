import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor_de as m


def product(price=100):
    return {'product_id': 'P', 'name': 'Jacket', 'url': 'https://example.test/jacket',
            'colors': {'red': 'Red'}, 'variants': {
                'V': {'product_id': 'P', 'variant_id': 'V', 'color_id': 'red', 'color': 'Red',
                      'name': 'Jacket', 'url': 'https://example.test/jacket', 'list_price': 100,
                      'current_price': price, 'discount_percent': 100-price,
                      'first_observed_at': '2026-11-01T00:00:00+00:00'}}}


class MonitorTests(unittest.TestCase):
    def test_first_baseline_is_not_new_and_discount_change_is_detected(self):
        current = {'P': product()}
        self.assertEqual(m.compare(None, current, 'now'), [])
        old = {'products': copy.deepcopy(current)}
        current['P'] = product(70)
        changes = m.compare(old, current, 'later')
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['new']['discount_percent'], 30)
        ended = m.compare({'products': copy.deepcopy(current)}, {'P': product()}, 'end')
        self.assertEqual(ended[0]['new']['discount_percent'], 0)

    def test_incomplete_capture_preserves_complete_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            state = {'sources': {'main_mens': {'products': {'P': product()}, 'checked_at': 'before'}}, 'promotions': {}}
            m.atomic(directory/'state.json', state)
            with patch.object(m, 'collect', return_value=({'P': {'id': 'P'}}, [])), \
                 patch.object(m, 'detail', side_effect=RuntimeError('HTTP 404')), \
                 patch.object(m, 'promotions', return_value={'mentions': []}):
                report = m.run(directory, ['main_mens'])
            after = json.loads((directory/'state.json').read_text())
            self.assertFalse(report['complete'])
            self.assertEqual(after['sources']['main_mens'], state['sources']['main_mens'])
            self.assertEqual(report['changes'], [])

    def test_outlet_placeholder_rejected(self):
        p = {'id': 'P', 'currencyCode': 'EUR', 'colourOptions': [{'id': 'red', 'label': 'Red'}],
             'variants': [{'id': 'V', 'colourId': 'red', 'price': 100, 'discountPrice': 999999}]}
        page = '<script id="__NEXT_DATA__">'+json.dumps({'props': {'pageProps': {'product': p}}})+'</script>'
        with patch.object(m, 'request', return_value=page):
            with self.assertRaisesRegex(ValueError, 'placeholder'):
                m.detail('https://outlet.arcteryx.com', {'id': 'P', 'slug': 'jacket'})

    def test_incomplete_pagination_cannot_become_baseline(self):
        data = {'result': {'data': {'json': {'pagination': {'offset': 0, 'totalCount': 2, 'hasMore': False},
                                                    'productList': [{'id': 'P'}]}}}}
        with patch.object(m, 'request', return_value=json.dumps(data)):
            with self.assertRaisesRegex(ValueError, 'count mismatch'):
                m.collect('https://arcteryx.com', 'mens/')

    def test_failed_notification_stays_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            file = directory/'outbox'/'one.json'
            m.atomic(file, {'status': 'pending', 'chunks': ['message'], 'sent_chunks': 0})
            with patch.dict(m.os.environ, {'TELEGRAM_BOT_TOKEN': 'test-only', 'TELEGRAM_CHAT_ID': 'test-only'}), \
                 patch.object(m, 'telegram_call', side_effect=RuntimeError('test delivery failure')):
                with self.assertRaises(RuntimeError):
                    m.send_pending(directory)
            self.assertEqual(json.loads(file.read_text())['status'], 'pending')

    def test_no_false_black_friday_from_script(self):
        parser = m.PromoParser()
        parser.feed('<script>black friday translation</script><p>Winter sale</p>')
        self.assertEqual(parser.text, ['Winter sale'])

    def test_recipient_discovery_uses_unique_private_chat(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            responses = [
                {'id': 10, 'username': 'test_bot'},
                [{'message': {'chat': {'id': 20, 'type': 'private'}, 'text': '/start'}}],
                {'id': 20, 'type': 'private'},
            ]
            with patch.dict(m.os.environ, {}, clear=True), patch.object(m, 'telegram_call', side_effect=responses):
                m.telegram_configure(directory)
                self.assertEqual(m.recipient(directory), '20')
            self.assertNotIn('token', (directory/'telegram-recipient.json').read_text())

    def test_ambiguous_recipient_is_not_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            responses = [{'id': 10, 'username': 'test_bot'}, [
                {'message': {'chat': {'id': 20, 'type': 'private'}}},
                {'message': {'chat': {'id': 21, 'type': 'private'}}},
            ]]
            with patch.dict(m.os.environ, {}, clear=True), patch.object(m, 'telegram_call', side_effect=responses):
                with self.assertRaisesRegex(RuntimeError, 'several people'):
                    m.telegram_configure(directory)
            self.assertFalse((directory/'telegram-recipient.json').exists())

    def test_token_error_reports_status_without_leaking_secret(self):
        token = '123456789' + ':' + 'x' * 35
        with patch.dict(m.os.environ, {'TELEGRAM_BOT_TOKEN': token}), \
             patch.object(m, 'request', side_effect=RuntimeError('HTTP 401')):
            with self.assertRaisesRegex(RuntimeError, 'Telegram HTTP 401') as context:
                m.telegram_call('getMe')
        self.assertNotIn(token, str(context.exception))

    def test_secret_must_not_be_botfather_message(self):
        with patch.dict(m.os.environ, {'TELEGRAM_BOT_TOKEN': 'Use this token to access the HTTP API:'}), \
             patch.object(m, 'request') as mocked:
            with self.assertRaisesRegex(RuntimeError, 'invalid format'):
                m.telegram_call('getMe')
        mocked.assert_not_called()


if __name__ == '__main__':
    unittest.main()
