"""Bounded public HTTP compatibility test, without secrets or notifications."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import monitoring_hours
import public_requests


def main():
    if not monitoring_hours.is_active():
        print('Quiet hours: no diagnostic requests.')
        return
    args = {'json': {'browserUserId': None, 'country': 'de', 'language': 'de',
                     'filters': {}, 'limit': 1, 'offset': 0, 'slug': 'mens/', 'sort': '',
                     'url': 'https://arcteryx.com/de/de/c/mens'},
            'meta': {'values': {'browserUserId': ['undefined']}}}
    urls = [('detail', 'https://arcteryx.com/de/de/shop/mens/beta-jacket-0868'),
            ('catalog', 'https://arcteryx.com/api/catalog.getProductListingPage?' +
             urllib.parse.urlencode({'input': json.dumps(args, separators=(',', ':'))}))]
    formats = [('python_default', {}),
               ('identified_monitor', {'User-Agent': public_requests.USER_AGENT}),
               ('html_compatible', {'User-Agent': 'Mozilla/5.0',
                                    'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
                                    'Accept-Language': 'de-DE,de;q=0.9'})]
    results = []
    for label, headers in formats:
        for kind, url in urls:
            monitoring_hours.ensure_active()
            record = {'format': label, 'kind': kind}
            stop = False
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
                    body = response.read(4096).decode('utf-8', 'replace')
                    record.update(status=response.status, content_type=response.headers.get('Content-Type'),
                                  product_json_marker='__NEXT_DATA__' in body,
                                  catalog_json_valid=(kind == 'catalog' and 'productList' in body))
            except urllib.error.HTTPError as error:
                controller = public_requests.PublicRequests()
                controller.failure(url, error)
                record.update(controller.diagnostics[0])
                # No more probing if explicit access denial, CAPTCHA or a rate limit is returned.
                stop = (error.code in (401, 403, 429) or record['headers'].get('retry-after') or
                        any(word in record['response_indicators'] for word in ('captcha', 'access denied')))
            except Exception as error:
                record['error'] = type(error).__name__
            results.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            if stop:
                print('Stopped: access restriction or rate limit; no bypass attempted.')
                return
            time.sleep(2)


if __name__ == '__main__':
    main()
