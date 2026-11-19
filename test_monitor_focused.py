import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor_de as common
import monitor_focused as focused
from test_monitor_de import product


CONFIGS = json.loads((focused.ROOT/'focused_skus.json').read_text())


def fixture(config, current=100):
    p = copy.deepcopy(product(current))
    p['product_id'] = config['id']
    p['name'] = config['label']
    p['url'] = focused.BASE + '/de/de/shop/' + config['slug']
    p['variants'] = {config['id']+'S': {**next(iter(p['variants'].values())),
                     'product_id': config['id'], 'variant_id': config['id']+'S',
                     'name': config['label'], 'url': p['url']}}
    return p


class FocusedTests(unittest.TestCase):
    def test_exact_seven_canonical_skus(self):
        self.assertEqual(len(CONFIGS), 7)
        self.assertEqual(len({c['id'] for c in CONFIGS}), 7)
        self.assertEqual(sum(c['slug'].startswith('womens/') for c in CONFIGS), 3)
        self.assertTrue(all('?' not in c['slug'] for c in CONFIGS))

    def test_one_message_per_sku_not_one_combined_message(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            report = focused.run(path, CONFIGS, fetch=lambda base, config: fixture(config))
            self.assertEqual(report['verified_skus'], 7)
            files = list((path/'outbox').glob('*.json'))
            self.assertEqual(len(files), 7)
            for file in files:
                msg = json.loads(file.read_text())
                self.assertEqual(len(msg['chunks']), 1)
                self.assertIn(msg['sku'], msg['chunks'][0])
                self.assertTrue(all(c['id'] not in msg['chunks'][0] for c in CONFIGS if c['id'] != msg['sku']))

    def test_failed_sku_preserves_last_price(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            focused.run(path, CONFIGS[:1], fetch=lambda base, config: fixture(config))
            before = json.loads((path/'focused-state.json').read_text())
            def fail(base, config):
                raise RuntimeError('HTTP 404')
            report = focused.run(path, CONFIGS[:1], fetch=fail)
            after = json.loads((path/'focused-state.json').read_text())
            self.assertEqual(before, after)
            self.assertFalse(report['complete'])
            self.assertIn('上次核实价格', report['messages'][0])
            self.assertIn('无法确认', report['messages'][0])

    def test_color_price_difference_and_change_are_explicit(self):
        config = CONFIGS[0]
        previous = fixture(config)
        current = fixture(config)
        old = next(iter(previous['variants'].values()))
        previous['variants']['BLUE'] = {**old, 'variant_id': 'BLUE', 'color': 'Blue', 'color_id': 'blue'}
        current['variants']['BLUE'] = {**previous['variants']['BLUE'], 'current_price': 70}
        message = focused.price_message(config, current, previous, common.now())
        self.assertIn('实时详情页价：€70–€100', message)
        self.assertIn('Blue：€100 → €70', message)
        self.assertIn('原价 €100，现价 €70', message)

    def test_unchanged_price_still_has_own_message(self):
        config = CONFIGS[0]
        p = fixture(config)
        message = focused.price_message(config, p, copy.deepcopy(p), common.now())
        self.assertIn('价格变化：较上次无变化', message)
        self.assertIn('原价：€100', message)
        self.assertIn('实时详情页价：€100', message)

    def test_seven_separate_acknowledged_sends_are_paced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            focused.run(path, CONFIGS, fetch=lambda base, config: fixture(config))
            with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'test-only', 'TELEGRAM_CHAT_ID': 'test-only'}), \
                 patch.object(common, 'telegram_call', return_value={'message_id': 1}) as api, \
                 patch.object(common.time, 'monotonic', return_value=0), \
                 patch.object(common.time, 'sleep') as sleep:
                common.send_pending(path, min_interval=1.1)
            self.assertEqual(api.call_count, 7)
            self.assertEqual(sleep.call_count, 6)
            self.assertTrue(all(json.loads(f.read_text())['status'] == 'sent' for f in (path/'outbox').glob('*.json')))


if __name__ == '__main__':
    unittest.main()
