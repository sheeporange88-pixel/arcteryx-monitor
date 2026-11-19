import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor_de as common
import monitor_focused as focused
import notification_policy as policy
from test_monitor_de import product
from test_monitor_focused import CONFIGS, fixture


class NotificationTests(unittest.TestCase):
    def test_hourly_boundary_deduplication_and_dst(self):
        state = {}
        self.assertIsNone(policy.decide(state, 'a', '2026-11-19T10:45:00+01:00'))
        self.assertEqual(policy.decide(state, 'a', '2026-11-19T10:55:00+01:00'), 'hourly_55')
        self.assertIsNone(policy.decide(state, 'a', '2026-11-19T11:02:00+01:00'))
        self.assertEqual(policy.decide(state, 'a', '2026-11-19T11:55:00+01:00'), 'hourly_55')
        self.assertEqual(policy.decide(state, 'b', '2026-07-19T08:55:00+00:00'), 'hourly_55')
        self.assertIsNone(policy.hourly_slot('2026-11-19T07:02:00+01:00'))

    def test_changes_do_not_wait_for_hourly_slot(self):
        self.assertEqual(policy.decide({}, 'a', '2026-11-19T10:05:00+01:00', changed=True), 'change')

    def test_seven_checks_persist_but_only_changed_sku_is_queued(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with patch.object(common, 'now', return_value='2026-11-19T10:05:00+01:00'):
                focused.run(path, CONFIGS, fetch=lambda b, c: fixture(c))
            with patch.object(common, 'now', return_value='2026-11-19T10:15:00+01:00'):
                unchanged = focused.run(path, CONFIGS, fetch=lambda b, c: fixture(c))
            self.assertEqual(unchanged['queued_messages'], 0)
            self.assertEqual(unchanged['verified_skus'], 7)
            with patch.object(common, 'now', return_value='2026-11-19T10:25:00+01:00'):
                changed = focused.run(path, CONFIGS, fetch=lambda b, c: fixture(c, 70 if c == CONFIGS[2] else 100))
            queued = [r for r in changed['results'] if r['notification_reason']]
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]['config']['id'], CONFIGS[2]['id'])
            self.assertIn('€100 → €70', changed['messages'][2])
            self.assertTrue((path/'latest-focused-prices.csv').exists())

    def test_hourly_seven_separate_messages_use_check_start_time(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with patch.object(common, 'now', return_value='2026-11-19T10:45:00+01:00'):
                focused.run(path, CONFIGS, fetch=lambda b, c: fixture(c))
            # :55 check finishes in the next hour, and still produces the hourly report.
            with patch.object(common, 'now', side_effect=['2026-11-19T10:55:00+01:00'] +
                              ['2026-11-19T11:01:00+01:00'] * 8):
                hourly = focused.run(path, CONFIGS, fetch=lambda b, c: fixture(c))
            self.assertEqual(hourly['queued_messages'], 7)
            for r in hourly['results']:
                self.assertEqual(r['notification_reason'], 'hourly_55')
            with patch.object(common, 'now', return_value='2026-11-19T11:02:00+01:00'):
                retry = focused.run(path, CONFIGS, fetch=lambda b, c: fixture(c))
            self.assertEqual(retry['queued_messages'], 0)

    def test_failure_not_spammed_and_recovery_notified_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with patch.object(common, 'now', return_value='2026-11-19T10:05:00+01:00'):
                focused.run(path, CONFIGS[:1], fetch=lambda b, c: fixture(c))
            def fail(b, c):
                raise RuntimeError('HTTP 404')
            with patch.object(common, 'now', return_value='2026-11-19T10:15:00+01:00'):
                failed = focused.run(path, CONFIGS[:1], fetch=fail)
            self.assertEqual(failed['queued_messages'], 1)
            with patch.object(common, 'now', return_value='2026-11-19T10:25:00+01:00'):
                again = focused.run(path, CONFIGS[:1], fetch=fail)
            self.assertEqual(again['queued_messages'], 0)
            with patch.object(common, 'now', return_value='2026-11-19T10:35:00+01:00'):
                recovered = focused.run(path, CONFIGS[:1], fetch=lambda b, c: fixture(c))
            self.assertEqual(recovered['queued_messages'], 1)
            self.assertFalse(recovered['results'][0]['price_changed'])

    def test_removal_of_discounted_variant_changes_displayed_price_range(self):
        old = fixture(CONFIGS[0])
        old['variants']['cheap'] = {**next(iter(old['variants'].values())), 'variant_id': 'cheap', 'current_price': 70}
        current = fixture(CONFIGS[0])
        self.assertTrue(policy.price_changed(old, current))
        self.assertIn('€70–€100 → €100', focused.price_message(CONFIGS[0], current, old, '2026-11-19T10:05:00+01:00'))
        # A new size at the same price is not a price alert.
        same = copy.deepcopy(current)
        same['variants']['new'] = {**next(iter(same['variants'].values())), 'variant_id': 'new'}
        self.assertFalse(policy.price_changed(current, same))

    def test_catalog_repeated_partial_source_change_does_not_spam(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            baseline = {'sources': {'main_mens': {'products': {'P': product()}, 'checked_at': 'before'}}, 'promotions': {}}
            common.atomic(path/'state.json', baseline)
            def fetch(base, tile):
                if tile['id'] == 'bad':
                    raise RuntimeError('HTTP 404')
                return product(70)
            with patch.object(common, 'collect', return_value=({'P': {'id': 'P'}, 'bad': {'id': 'bad'}}, [])), \
                 patch.object(common, 'detail', side_effect=fetch), \
                 patch.object(common, 'promotions', return_value={'mentions': []}), \
                 patch.object(common, 'now', return_value='2026-11-19T10:05:00+01:00'):
                first = common.run(path, ['main_mens'])
            self.assertEqual(first['notification_reason'], 'change')
            with patch.object(common, 'collect', return_value=({'P': {'id': 'P'}, 'bad': {'id': 'bad'}}, [])), \
                 patch.object(common, 'detail', side_effect=fetch), \
                 patch.object(common, 'promotions', return_value={'mentions': []}), \
                 patch.object(common, 'now', return_value='2026-11-19T10:15:00+01:00'):
                second = common.run(path, ['main_mens'])
            self.assertEqual(len(second['changes']), 1)
            self.assertEqual(second['queued_messages'], 0)
            self.assertEqual(json.loads((path/'state.json').read_text())['sources'], baseline['sources'])


if __name__ == '__main__':
    unittest.main()
