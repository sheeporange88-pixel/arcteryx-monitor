#!/usr/bin/env python3
"""Seven independent SKU watches; changes now, unchanged prices on the :55 check."""
import argparse
from collections import defaultdict
import concurrent.futures
import csv
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import monitor_de as common
import notification_policy as notifications

ROOT = Path(__file__).resolve().parent
BASE = 'https://arcteryx.com'


def euros(value):
    return '€' + format(value, '.2f').rstrip('0').rstrip('.')


def price_range(values):
    values = sorted(set(values))
    return euros(values[0]) if len(values) == 1 else euros(values[0]) + '–' + euros(values[-1])


def price_message(config, product, previous, stamp, error=None):
    local = datetime.fromisoformat(stamp).astimezone(ZoneInfo('Europe/Berlin'))
    url = BASE + '/de/de/shop/' + config['slug']
    lines = [config['label'] + ' · ' + config['id'], local.strftime('%Y-%m-%d %H:%M 柏林时间')]
    if error:
        lines.append('本次价格读取失败：' + error)
        if previous:
            records = list(previous['variants'].values())
            lines.extend(['上次核实原价：' + price_range(v['list_price'] for v in records),
                          '上次核实价格：' + price_range(v['current_price'] for v in records),
                          '上次核实时间：' + previous.get('checked_at', '未知')])
        lines.extend(['当前价格及变化：无法确认；保留上次记录。', url])
        return '\n'.join(lines)
    records = list(product['variants'].values())
    lines.extend(['原价：' + price_range(v['list_price'] for v in records),
                  '实时详情页价：' + price_range(v['current_price'] for v in records)])
    if previous is None:
        lines.append('价格变化：首次记录，尚无上次价格可比较。')
    else:
        changes, seen = [], set()
        unmatched = 0
        for variant in records:
            old = previous['variants'].get(variant['variant_id'])
            if not old:
                unmatched += 1
                continue
            if (old['list_price'], old['current_price']) == (variant['list_price'], variant['current_price']):
                continue
            key = (variant['color_id'], old['list_price'], old['current_price'], variant['list_price'], variant['current_price'])
            if key in seen:
                continue
            seen.add(key)
            changes.append(variant['color'] + '：' + euros(old['current_price']) + ' → ' + euros(variant['current_price'])
                           + ('；原价 ' + euros(old['list_price']) + ' → ' + euros(variant['list_price'])
                              if old['list_price'] != variant['list_price'] else ''))
        if changes:
            lines.append('价格变化（较上次）：')
            lines.extend(changes)
        elif notifications.price_changed(previous, product):
            old_records = list(previous['variants'].values())
            lines.append('价格范围变化（颜色/尺码组合调整）：原价 ' +
                         price_range(v['list_price'] for v in old_records) + ' → ' +
                         price_range(v['list_price'] for v in records) + '；现价 ' +
                         price_range(v['current_price'] for v in old_records) + ' → ' +
                         price_range(v['current_price'] for v in records))
        else:
            lines.append('价格变化：较上次无变化。')
        if unmatched:
            lines.append('其中新增颜色/尺码记录没有上次可比价格。')
    groups = defaultdict(list)
    for v in records:
        groups[(v['list_price'], v['current_price'])].append(v)
    if len(groups) == 1:
        lines.append('全部已展示颜色/尺码同价。')
    else:
        lines.append('颜色/尺码价格：')
        for (regular, current), variants in sorted(groups.items()):
            by_color = defaultdict(list)
            for variant in variants:
                by_color[variant['color']].append(variant.get('size'))
            labels = []
            for color, sizes in sorted(by_color.items()):
                all_color_count = sum(v['color'] == color for v in records)
                size_label = '' if len(sizes) == all_color_count else '（' + (','.join(sorted(set(sizes))) if all(sizes) else '部分尺码') + '）'
                labels.append(color + size_label)
            lines.append('、'.join(labels) + '：原价 ' + euros(regular) + '，现价 ' + euros(current))
    lines.append(url)
    message = '\n'.join(lines)
    if len(message) > 3500:
        # Preserve truthful price ranges rather than splitting one SKU across messages.
        prefix = lines[:4]
        prefix.append('颜色/尺码价格组较多；完整价格及变化明细见本轮报表。')
        server, repo, run_id = (os.environ.get(k) for k in ('GITHUB_SERVER_URL', 'GITHUB_REPOSITORY', 'GITHUB_RUN_ID'))
        if server and repo and run_id:
            prefix.append(server + '/' + repo + '/actions/runs/' + run_id)
        prefix.append(url)
        message = '\n'.join(prefix)
    return message


def run(data_dir, configs, fetch=common.detail):
    data_dir.mkdir(parents=True, exist_ok=True)
    common.PUBLIC_REQUESTS = common.public_requests.PublicRequests()
    health_path = data_dir / 'request-cooldowns.json'
    if health_path.exists():
        common.PUBLIC_REQUESTS.restore(json.loads(health_path.read_text()))
    stamp = common.now()
    state_path = data_dir / 'focused-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'products': {}}
    notice_path = data_dir / 'notification-state.json'
    notices = json.loads(notice_path.read_text()) if notice_path.exists() else {}
    prior_path = data_dir / 'latest-focused-run.json'
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {}
    prior_errors = {r['config']['id']: r['error'] for r in prior.get('results', [])}
    report = {'started_at': stamp, 'complete': True, 'messages': [], 'results': [], 'queued_messages': 0}
    fetched = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, BASE, config): config for config in configs}
        for future in concurrent.futures.as_completed(futures):
            config = futures[future]
            try:
                fetched[config['id']] = (future.result(), None)
            except common.monitoring_hours.QuietHours:
                raise
            except Exception as error:
                fetched[config['id']] = (None, str(error)[:200])
    batch = datetime.fromisoformat(stamp).strftime('%Y%m%dT%H%M%S%fZ')
    for config in configs:
        product, error = fetched[config['id']]
        previous = state['products'].get(config['id'])
        checked_at = common.now()
        message = price_message(config, product, previous, checked_at, error)
        row = {'config': config, 'checked_at': checked_at, 'error': error, 'product': product}
        changed = notifications.price_changed(previous, product)
        status_changed = (config['id'] in prior_errors and prior_errors[config['id']] != error)
        reason = notifications.decide(notices, config['id'], stamp, changed=changed,
                                      initial=(previous is None and (product is not None or config['id'] not in prior_errors)),
                                      status_changed=status_changed)
        row.update(price_changed=changed, notification_reason=reason)
        if product:
            product['checked_at'] = checked_at
            product['first_observed_at'] = previous.get('first_observed_at', stamp) if previous else stamp
            for vid, variant in product['variants'].items():
                old = previous['variants'].get(vid) if previous else None
                variant['first_observed_at'] = old.get('first_observed_at', stamp) if old else stamp
            state['products'][config['id']] = product
        else:
            report['complete'] = False
        # The outbox entry intentionally contains exactly one SKU and one message.
        if reason:
            common.atomic(data_dir/'outbox'/(batch+'-'+config['id']+'.json'),
                          {'status': 'pending', 'chunks': [message], 'sent_chunks': 0,
                           'sku': config['id'], 'created_at': checked_at, 'reason': reason})
            report['queued_messages'] += 1
        report['messages'].append(message)
        report['results'].append(row)
        print(message + '\n', flush=True)
    report['finished_at'] = common.now()
    report['request_health'] = common.PUBLIC_REQUESTS.snapshot()
    common.atomic(health_path, common.PUBLIC_REQUESTS.cooldowns)
    report['verified_skus'] = sum(row['error'] is None for row in report['results'])
    report['colors'] = sum(len(row['product']['colors']) for row in report['results'] if row['product'])
    report['variants'] = sum(len(row['product']['variants']) for row in report['results'] if row['product'])
    common.atomic(data_dir/'focused-state.json', state)
    common.atomic(notice_path, notices)
    common.atomic(data_dir/'latest-focused-run.json', report)
    common.atomic(data_dir/'runs'/(batch+'.json'), report)
    (data_dir/'latest-focused-report.txt').write_text('\n\n'.join(report['messages']))
    fields = ['product_id', 'name', 'color', 'size', 'size_id', 'list_price', 'current_price', 'currency', 'url', 'first_observed_at', 'checked_at']
    with (data_dir/'latest-focused-prices.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report['results']:
            if row['product']:
                for v in row['product']['variants'].values():
                    writer.writerow({k: v.get(k) for k in fields})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data-focused')
    parser.add_argument('--notify', action='store_true')
    args = parser.parse_args()
    if not common.monitoring_hours.is_active():
        print('Berlin 00:00–07:00: focused SKU monitor paused; no fetching or Telegram sends.')
        return
    configs = json.loads((ROOT/'focused_skus.json').read_text())
    report = run(args.data_dir, configs)
    if args.notify:
        common.send_pending(args.data_dir, min_interval=1.1)
        pending = []
        for file in (args.data_dir/'outbox').glob('*.json'):
            item = json.loads(file.read_text())
            if item.get('status') != 'sent':
                pending.append(file.name)
        print(json.dumps({'focused_skus': len(configs), 'verified_skus': report['verified_skus'],
                          'telegram_queued_messages': report['queued_messages'],
                          'telegram_pending_messages': len(pending),
                          'device_delivery_confirmed': False}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except common.monitoring_hours.QuietHours as error:
        print(str(error))
