"""Portable Linux runner; public preflight, durable state, and separate delivery."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

import monitor_de as monitor
import public_requests

ROOT = Path(__file__).resolve().parent


def preflight():
    monitor.monitoring_hours.ensure_active()
    monitor.PUBLIC_REQUESTS = public_requests.PublicRequests(interval=2)
    configs = json.loads((ROOT/'focused_skus.json').read_text())
    results = []
    for config in configs:
        product = monitor.detail('https://arcteryx.com', config)
        results.append({'id': product['product_id'], 'colors': len(product['colors']),
                        'variants': len(product['variants'])})
    catalogs = {}
    for name, (base, slug) in monitor.SOURCES.items():
        products, pages = monitor.collect(base, slug)
        sample = monitor.detail(base, next(iter(products.values())))
        catalogs[name] = {'listed_products': len(products), 'pages': len(pages),
                          'sample_product': sample['product_id']}
    print(json.dumps({'preflight_only': True, 'focused_verified': results, 'catalogs': catalogs,
                      'note': 'Seven SKU prices, all catalog pages and one detail sample per catalog; not a full baseline.'},
                     ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--data-root', type=Path, default=ROOT/'server-data')
    args = parser.parse_args()
    if not monitor.monitoring_hours.is_active():
        print('Berlin quiet hours: no requests or delivery.')
        return
    if args.preflight:
        preflight()
        return
    args.data_root.mkdir(parents=True, exist_ok=True)
    with (args.data_root/'runner.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Previous check still active; skip overlapping run.')
            return
        public_env = {k: v for k, v in os.environ.items() if not k.startswith('TELEGRAM_')}
        failures = []
        for name, script, timeout in [('focused', 'monitor_focused.py', 180), ('de', 'monitor_de.py', 900)]:
            monitor.monitoring_hours.ensure_active()
            folder = args.data_root/('data-'+name)
            try:
                subprocess.run([sys.executable, str(ROOT/script), '--data-dir', str(folder)],
                               check=True, timeout=timeout, env=public_env)
                monitor.monitoring_hours.ensure_active()
                subprocess.run([sys.executable, str(ROOT/'monitor_de.py'), '--data-dir', str(folder),
                                '--telegram-configure'], check=True, timeout=90)
                subprocess.run([sys.executable, str(ROOT/'monitor_de.py'), '--data-dir', str(folder),
                                '--send-pending', '--send-interval', '1.1'], check=True, timeout=120)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                failures.append(name)
                print(name + ': execution/delivery failed; keep durable baseline and pending outbox.', flush=True)
        if failures:
            raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except monitor.monitoring_hours.QuietHours as error:
        print(str(error))
