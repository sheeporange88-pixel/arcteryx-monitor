"""Bounded sequential production-price diagnostic; no secrets or notifications."""
import json
import time
import monitor_de as monitor
from pathlib import Path
import public_requests


def main():
    if not monitor.monitoring_hours.is_active():
        print('Quiet hours: no requests.')
        return
    configs = json.loads(Path('focused_skus.json').read_text())
    configs.append({'id': 'X000010873', 'slug': 'mens/rush-bib-pant-0873'})
    for config in configs:
        monitor.monitoring_hours.ensure_active()
        monitor.PUBLIC_REQUESTS = public_requests.PublicRequests(interval=2)
        record = {'id': config['id']}
        try:
            product = monitor.detail('https://arcteryx.com', config)
            variants = list(product['variants'].values())
            record.update(status=200, verified_id=product['product_id'],
                          colors=len(product['colors']), variants=len(variants),
                          current_min=min(v['current_price'] for v in variants),
                          current_max=max(v['current_price'] for v in variants))
        except Exception as error:
            record['error'] = str(error)[:200]
            record['request_health'] = monitor.PUBLIC_REQUESTS.snapshot()
        print(json.dumps(record, ensure_ascii=False), flush=True)
        health = monitor.PUBLIC_REQUESTS.snapshot()
        if any(d['status'] in (401,403,429) or d['headers'].get('retry-after') or
               any(w in d['response_indicators'] for w in ('captcha','access denied'))
               for d in health['diagnostics']):
            print('Stopped on explicit access restriction or rate limit.')
            return
        time.sleep(2)


if __name__ == '__main__':
    main()
