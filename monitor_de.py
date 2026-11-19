#!/usr/bin/env python3
"""German-market adapter for TongTong0828/arcteryx-monitor; standard library only.

Read public paginated catalog APIs and product JSON embedded in public pages.
Never trust outlet listing price sentinels. Baselines advance per complete source.
Telegram delivery is explicit (--notify), acknowledged, and stored in an outbox.
"""
import argparse
import concurrent.futures
import csv
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
import monitoring_hours
import notification_policy as notifications
import public_requests

PUBLIC_REQUESTS = public_requests.PublicRequests()

ROOT = Path(__file__).resolve().parent
SOURCES = {
    'main_mens': ('https://arcteryx.com', 'mens/'),
    'main_new': ('https://arcteryx.com', 'mens/new-arrivals'),
    'outlet_mens': ('https://outlet.arcteryx.com', 'mens/'),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def request(url, body=None, retry=True):
    data = json.dumps(body).encode() if body is not None else None
    host = urllib.parse.urlsplit(url).hostname
    headers = {'Content-Type': 'application/json'} if data else {}
    if host in public_requests.HOSTS:
        headers.update({'User-Agent': public_requests.USER_AGENT,
                        'Accept': 'application/json' if '/api/' in urllib.parse.urlsplit(url).path
                                  else 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'de-DE,de;q=0.9,en;q=0.5'})
    req = urllib.request.Request(url, data=data, headers=headers)
    for attempt in range(2 if retry else 1):
        if urllib.parse.urlsplit(url).hostname in ('arcteryx.com', 'outlet.arcteryx.com', 'api.telegram.org'):
            monitoring_hours.ensure_active()
        PUBLIC_REQUESTS.before(url)
        if host in public_requests.HOSTS:
            monitoring_hours.ensure_active()
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            message = PUBLIC_REQUESTS.failure(url, error)
            # Do not retry 429; honour server rate limits by ending this request.
            if not retry or error.code not in (500, 502, 503, 504) or attempt:
                raise RuntimeError(message) from None
        except (urllib.error.URLError, TimeoutError):
            if not retry or attempt:
                raise RuntimeError('Network connection or timeout error') from None
        time.sleep(1)


def collect(base, slug):
    products, pages, offset, expected = {}, [], 0, None
    while True:
        args = {'json': {'browserUserId': None, 'country': 'de', 'language': 'de',
                         'filters': {}, 'limit': 100, 'offset': offset, 'slug': slug,
                         'sort': '', 'url': base + '/de/de/c/' + slug.rstrip('/')},
                'meta': {'values': {'browserUserId': ['undefined']}}}
        url = base + '/api/catalog.getProductListingPage?' + urllib.parse.urlencode(
            {'input': json.dumps(args, separators=(',', ':'))})
        data = json.loads(request(url))['result']['data']['json']
        page, items = data['pagination'], data['productList']
        expected = page['totalCount'] if expected is None else expected
        if page['totalCount'] != expected or page['offset'] != offset:
            raise ValueError('Pagination changed during capture; retry next run')
        pages.append(page)
        for item in items:
            if item['id'] in products:
                raise ValueError('Duplicate product across pages')
            products[item['id']] = item
        if not page['hasMore']:
            break
        if not items:
            raise ValueError('Empty page before end of catalog')
        offset += len(items)
        if len(pages) > 100:
            raise ValueError('Pagination limit exceeded')
    if len(products) != expected or not products:
        raise ValueError('Catalog count mismatch or unexpected empty catalog')
    return products, pages


def detail(base, item):
    slug = item['slug'].lstrip('/')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', slug) or '..' in slug.split('/'):
        raise ValueError('Invalid product slug')
    url = base + '/de/de/shop/' + slug
    text = request(url)
    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    if not match:
        raise ValueError('Missing public product JSON')
    p = json.loads(match.group(1))['props']['pageProps']['product']
    p = json.loads(p) if isinstance(p, str) else p
    if p['id'] != item['id'] or p['currencyCode'] != 'EUR':
        raise ValueError('Product ID or EUR currency mismatch')
    colors = {str(c['id']): c['label'] for c in p['colourOptions']}
    sizes = {str(s['id']): s.get('label') for s in p.get('sizeOptions', []) if isinstance(s, dict) and s.get('id') is not None}
    variants, color_prices = {}, {}
    for v in p['variants']:
        regular, current = v.get('price'), v.get('discountPrice')
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                   for x in (regular, current)) or not (0 <= current <= regular < 100000):
            raise ValueError('Invalid or placeholder variant prices')
        if regular <= 0:
            raise ValueError('Missing regular price')
        color_id = str(v['colourId'])
        if color_id not in colors or v['id'] in variants:
            raise ValueError('Unknown color or duplicate variant')
        record = {'product_id': p['id'], 'name': p['marketingName'], 'url': url,
                  'color_id': color_id, 'color': colors[color_id], 'variant_id': v['id'],
                  'size_id': str(v.get('sizeId', '')), 'currency': 'EUR',
                  'size': sizes.get(str(v.get('sizeId', ''))),
                  'price_source': 'sku_detail_variant',
                  'list_price': regular, 'current_price': current,
                  'discount_percent': round((regular-current)/regular*100, 2),
                  'checked_at': now()}
        variants[v['id']] = record
        color_prices[color_id] = colors[color_id]
    if set(color_prices) != set(colors) or not variants:
        raise ValueError('Not every color has verified variants')
    listed = {c['label'] for c in item.get('colourOptions', [])}
    if listed and listed != set(colors.values()):
        raise ValueError('Listing and detail color options differ')
    listed_range = item.get('priceRange')
    detail_range = {'minDiscountPrice': min(v['current_price'] for v in variants.values()),
                    'maxDiscountPrice': max(v['current_price'] for v in variants.values())}
    listing_price_lag = bool(listed_range and any(
        listed_range.get(k) != detail_range[k] for k in detail_range))
    return {'product_id': p['id'], 'name': p['marketingName'], 'url': url,
            'colors': colors, 'variants': variants,
            'listing_price_range': listed_range, 'detail_price_range': detail_range,
            'listing_detail_price_disagreement': listing_price_lag}


def compare(previous, current, stamp):
    events, seen = [], set()
    old_products = previous['products'] if previous else {}
    for pid, product in current.items():
        old = old_products.get(pid)
        product['first_observed_at'] = old.get('first_observed_at', stamp) if old else stamp
        if previous and not old:
            events.append({'kind': 'new_product', 'name': product['name'],
                           'url': product['url'], 'product_id': pid, 'detected_at': stamp})
        for vid, v in product['variants'].items():
            oldv = old['variants'].get(vid) if old else None
            v['first_observed_at'] = oldv.get('first_observed_at', stamp) if oldv else stamp
            if previous and old and not oldv:
                key = ('new_variant', pid, v['color_id'])
                if key not in seen:
                    events.append({'kind': 'new_color_or_size', 'name': v['name'],
                                   'color': v['color'], 'url': v['url'],
                                   'current_price': v['current_price'], 'detected_at': stamp})
                    seen.add(key)
            if oldv and any(v[k] != oldv[k] for k in ('list_price', 'current_price', 'discount_percent')):
                key = ('price', pid, v['color_id'], oldv['list_price'], oldv['current_price'], v['list_price'], v['current_price'])
                if key in seen:
                    continue
                seen.add(key)
                events.append({'kind': 'price_or_discount', 'name': v['name'],
                               'color': v['color'], 'url': v['url'],
                               'old': {k: oldv[k] for k in ('list_price', 'current_price', 'discount_percent')},
                               'new': {k: v[k] for k in ('list_price', 'current_price', 'discount_percent')},
                               'detected_at': stamp})
    return events


class PromoParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1

    def handle_data(self, text):
        if not self.skip and text.strip():
            self.text.append(text.strip())


def promotions(base):
    url = base + '/de/de'
    parser = PromoParser()
    parser.feed(request(url))
    phrases = sorted({re.sub(r'\s+', ' ', t)[:400] for t in parser.text
                      if re.search(r'black\s*[-–]?\s*(friday|week)|cyber\s*monday', t, re.I)})
    return {'url': url, 'mentions': phrases, 'checked_at': now(),
            'note': 'Only explicit homepage wording; no Black Friday claim from ordinary outlet discounts.'}


def summary(report):
    local = datetime.fromisoformat(report['finished_at']).astimezone(ZoneInfo('Europe/Berlin'))
    lines = ['始祖鸟德国站：黑五折扣与上新', local.strftime('%Y-%m-%d %H:%M 柏林时间')]
    lines.append('逐款读取SKU详情页的颜色/尺码价格；总览页价格不变也检查。')
    for key, source in report['sources'].items():
        label = {'main_mens': '官网男士', 'main_new': '官网新品', 'outlet_mens': 'Outlet男士'}[key]
        if not source['verified_products']:
            lines.append(f"{label}：已核验0/{source.get('listed_products', '?')}款；本轮价格与折扣无法确认。")
        else:
            lines.append(f"{label}：{source['verified_products']}/{source.get('listed_products', '?')}款，"
                         f"{source['colors']}颜色，{source['variants']}尺码颜色组合；"
                         f"{'完整' if source['complete'] else '覆盖不全'}；已核验部分折扣颜色{source['discounted_colors']}个")
        if source['baseline_established']:
            lines.append('首次完整基准已建立；现有商品与折扣不视为新变化。')
        for error in source['errors'][:3]:
            lines.append('读取失败：' + error.get('product_id', '') + ' ' + error['error'])
    changes = report['changes']
    if not changes:
        if any(source['baseline_established'] for source in report['sources'].values()):
            lines.append('首次基准已保存，后续检查将与此比较；本轮不将现有商品视为新品。')
        else:
            lines.append('本轮无已确认变化。' if not report['complete'] else '本轮检查完成，未发现新品、价格或折扣变化。')
    else:
        lines.append(f'发现{len(changes)}项变化（完整明细见本轮JSON记录）：')
        for event in changes:
            lines.append(event['kind'] + ' ' + event.get('name', event['source']))
            if 'color' in event:
                lines.append('颜色：' + event['color'])
            if 'old' in event:
                lines.append(f"€{event['old']['current_price']} → €{event['new']['current_price']}；"
                             f"折扣{event['old']['discount_percent']}% → {event['new']['discount_percent']}%")
            if 'new_mentions' in event:
                lines.append('新活动文字：' + ' / '.join(event['new_mentions']))
            if event.get('url'):
                lines.append(event['url'])
    promo = [phrase for p in report['promotions'].values() if not p.get('error') for phrase in p['mentions']]
    promo_errors = [name for name, p in report['promotions'].items() if p.get('error')]
    lines.append('官网黑五文字：' + (' / '.join(promo) if promo else
                 '首页读取失败，无法确认活动文字。' if promo_errors else
                 '本轮首页未检出明确黑五活动文字；不代表全站没有活动。'))
    if promo and promo_errors:
        lines.append('部分首页读取失败，活动文字覆盖不全。')
    paused = report.get('request_health', {}).get('paused_hosts', {})
    if paused:
        lines.append('服务器拒绝访问，已暂停本轮后续请求：' + '、'.join(paused) + '；不会连续重试。')
    if not report['complete']:
        lines.append('不完整来源未覆盖其原有完整基准，未把失败商品当作下架。')
    return '\n'.join(lines)


def telegram_call(method, payload=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise RuntimeError('Missing TELEGRAM_BOT_TOKEN')
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]{20,}', token):
        raise RuntimeError('TELEGRAM_BOT_TOKEN has invalid format: save only the token, without quotes, bot username, or BotFather message text.')
    try:
        response = json.loads(request('https://api.telegram.org/bot' + token + '/' + method,
                                      payload, retry=False))
    except monitoring_hours.QuietHours:
        raise
    except RuntimeError as error:
        message = str(error)
        if re.fullmatch(r'HTTP [0-9]{3}', message):
            guidance = {
                'HTTP 401': 'Token is invalid or revoked. Update the TELEGRAM_BOT_TOKEN repository secret.',
                'HTTP 404': 'Bot token or Telegram API route is not valid. Check the repository secret.',
                'HTTP 409': 'Another bot process or webhook is using getUpdates.',
                'HTTP 429': 'Telegram rate limited this request. Retry later.',
            }.get(message, 'Telegram rejected the request; check method and recipient permissions.')
            raise RuntimeError('Telegram ' + message + ' during ' + method + '. ' + guidance) from None
        raise RuntimeError('Telegram connection or timeout error during ' + method + '; token and URL suppressed.') from None
    except Exception:
        raise RuntimeError('Telegram response could not be parsed; token and URL suppressed.') from None
    if not response.get('ok'):
        code = response.get('error_code', 'unknown')
        safe_code = str(code) if isinstance(code, int) else 'unknown'
        raise RuntimeError('Telegram API error ' + safe_code + ' during ' + method + '; no token or response text logged.')
    return response['result']


def telegram_check(data_dir):
    me = telegram_call('getMe')
    chat_id = recipient(data_dir)
    if not chat_id:
        raise RuntimeError('Missing TELEGRAM_CHAT_ID')
    chat = telegram_call('getChat', {'chat_id': chat_id})
    outcome = {'checked_at': now(), 'read_access_verified': True,
               'bot_username': me.get('username'), 'chat_type': chat.get('type'),
               'device_delivery_confirmed': False}
    atomic(data_dir / 'telegram-access.json', outcome)
    print(json.dumps(outcome, ensure_ascii=False))


def recipient(data_dir):
    if os.environ.get('TELEGRAM_CHAT_ID'):
        return os.environ['TELEGRAM_CHAT_ID']
    path = data_dir / 'telegram-recipient.json'
    return json.loads(path.read_text()).get('chat_id') if path.exists() else None


def telegram_configure(data_dir):
    """Resolve one private chat after /start without printing messages or credentials."""
    me = telegram_call('getMe')
    path = data_dir / 'telegram-recipient.json'
    saved = json.loads(path.read_text()) if path.exists() else {}
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not chat_id and saved.get('bot_id') == me['id']:
        chat_id = saved.get('chat_id')
    if not chat_id:
        updates = telegram_call('getUpdates', {'timeout': 0, 'limit': 100})
        chats = {str(u['message']['chat']['id']) for u in updates
                 if u.get('message', {}).get('chat', {}).get('type') == 'private'}
        if len(chats) != 1:
            raise RuntimeError('Send /start to your bot, then retry. If several people used the bot, set TELEGRAM_CHAT_ID in Secrets.')
        chat_id = chats.pop()
    chat = telegram_call('getChat', {'chat_id': chat_id})
    if chat.get('type') != 'private':
        raise RuntimeError('Default setup expects a private recipient, not a group or channel')
    atomic(path, {'bot_id': me['id'], 'chat_id': str(chat['id']), 'verified_at': now()})
    print('Telegram read access verified for @' + me.get('username', 'bot') + '; recipient saved privately.')


def telegram_discover():
    me = telegram_call('getMe')
    updates = telegram_call('getUpdates', {'timeout': 0, 'limit': 100})
    chats = {}
    for update in updates:
        for key in ('message', 'channel_post', 'my_chat_member'):
            chat = update.get(key, {}).get('chat')
            if chat:
                chats[str(chat['id'])] = {'chat_id': chat['id'], 'type': chat.get('type')}
    print(json.dumps({'bot_username': me.get('username'), 'chats': list(chats.values()),
                      'note': 'Use the chat ID for your intended recipient. Message text and credentials are not printed.'},
                     ensure_ascii=False))


def send_pending(data_dir, min_interval=0):
    outbox = data_dir / 'outbox'
    files = sorted(outbox.glob('*.json')) if outbox.exists() else []
    if not files:
        return
    chat_id = recipient(data_dir)
    if not os.environ.get('TELEGRAM_BOT_TOKEN') or not chat_id:
        raise RuntimeError('Telegram credentials not configured; reports remain in outbox')
    last_sent = None
    for file in files:
        record = json.loads(file.read_text())
        if record['status'] == 'sent':
            continue
        # Save each acknowledged chunk to avoid replaying already sent chunks.
        for index in range(record.get('sent_chunks', 0), len(record['chunks'])):
            if last_sent is not None and min_interval > 0:
                time.sleep(max(0, min_interval - (time.monotonic() - last_sent)))
            result = telegram_call('sendMessage', {'chat_id': chat_id,
                                   'text': record['chunks'][index], 'disable_web_page_preview': True})
            record.setdefault('message_ids', []).append(result['message_id'])
            record['sent_chunks'] = index + 1
            atomic(file, record)
            last_sent = time.monotonic()
        record.update(status='sent', api_accepted_at=now(), device_delivery_confirmed=False)
        atomic(file, record)


def chunks(text, limit=3500):
    parts = []
    while len(text) > limit:
        pos = text.rfind('\n', 0, limit)
        pos = pos if pos > 0 else limit
        parts.append(text[:pos])
        text = text[pos:].lstrip('\n')
    if text:
        parts.append(text)
    return parts


def run(data_dir, source_names):
    global PUBLIC_REQUESTS
    PUBLIC_REQUESTS = public_requests.PublicRequests()
    health_path = data_dir / 'request-cooldowns.json'
    if health_path.exists():
        PUBLIC_REQUESTS.restore(json.loads(health_path.read_text()))
    started = now()
    notice_path = data_dir / 'notification-state.json'
    notices = json.loads(notice_path.read_text()) if notice_path.exists() else {}
    prior_path = data_dir / 'latest-run.json'
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else None
    state_path = data_dir / 'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'sources': {}, 'promotions': {}}
    report = {'started_at': started, 'sources': {}, 'changes': [], 'promotions': {}, 'complete': True}
    for name in source_names:
        base, slug = SOURCES[name]
        source = {'url': base + '/de/de/c/' + slug.rstrip('/'), 'complete': False,
                  'errors': [], 'verified_products': 0, 'colors': 0, 'variants': 0,
                  'discounted_colors': 0, 'baseline_established': False}
        current = {}
        try:
            items, pages = collect(base, slug)
            source.update(listed_products=len(items), pages=pages)
            print(f'{name}: {len(items)} listing products', flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = {executor.submit(detail, base, item): pid for pid, item in items.items()}
                for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                    pid = futures[future]
                    try:
                        current[pid] = future.result()
                    except monitoring_hours.QuietHours:
                        raise
                    except Exception as error:
                        source['errors'].append({'product_id': pid, 'error': str(error)[:200]})
                    if index % 60 == 0 or index == len(futures):
                        print(f'{name}: {index}/{len(futures)} attempted, {len(source["errors"])} failures', flush=True)
            source['complete'] = not source['errors'] and len(current) == len(items)
            previous = state['sources'].get(name)
            events = compare(previous, current, started)
            for event in events:
                event['source'] = name
            report['changes'].extend(events)
            if source['complete']:
                source['baseline_established'] = previous is None
                state['sources'][name] = {'products': current, 'checked_at': started}
        except monitoring_hours.QuietHours:
            raise
        except Exception as error:
            source['errors'].append({'error': str(error)[:200]})
        source['verified_products'] = len(current)
        source['colors'] = sum(len(p['colors']) for p in current.values())
        source['variants'] = sum(len(p['variants']) for p in current.values())
        source['discounted_colors'] = len({(pid, v['color_id']) for pid, p in current.items()
                                          for v in p['variants'].values() if v['current_price'] < v['list_price']})
        source['products'] = current
        report['sources'][name] = source
        report['complete'] = report['complete'] and source['complete']
    for name, base in [('main', SOURCES['main_mens'][0]), ('outlet', SOURCES['outlet_mens'][0])]:
        try:
            promo = promotions(base)
            previous = state['promotions'].get(name)
            if previous is not None and promo['mentions'] != previous['mentions']:
                report['changes'].append({'kind': 'black_friday_homepage_text', 'source': name,
                                          'url': promo['url'], 'old_mentions': previous['mentions'],
                                          'new_mentions': promo['mentions'], 'detected_at': now()})
            state['promotions'][name] = promo
            report['promotions'][name] = promo
        except monitoring_hours.QuietHours:
            raise
        except Exception as error:
            report['promotions'][name] = {'error': str(error)[:200]}
    report['finished_at'] = now()
    report['request_health'] = PUBLIC_REQUESTS.snapshot()
    atomic(health_path, PUBLIC_REQUESTS.cooldowns)
    event_ids = {notifications.fingerprint(e) for e in report['changes']}
    previous_ids = set(notices.get('event_ids', [notifications.fingerprint(e) for e in
                                               (prior or {}).get('changes', [])]))
    reason = notifications.decide(
        notices, 'catalog', started, changed=bool(event_ids - previous_ids),
        initial=any(s['baseline_established'] for s in report['sources'].values()),
        status_changed=prior is not None and notifications.report_errors(prior) != notifications.report_errors(report))
    notices['event_ids'] = sorted(event_ids)
    report['notification_reason'] = reason
    report['queued_messages'] = int(reason is not None)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    atomic(data_dir / 'runs' / (stamp + '.json'), report)
    atomic(data_dir / 'latest-run.json', report)
    atomic(state_path, state)
    text = summary(report)
    (data_dir / 'latest-report.txt').write_text(text)
    fields = ['source', 'product_id', 'name', 'color_id', 'color', 'variant_id', 'size_id', 'currency',
              'list_price', 'current_price', 'discount_percent', 'url', 'first_observed_at', 'checked_at']
    with (data_dir / 'latest-prices.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for name, source in report['sources'].items():
            for product in source['products'].values():
                for variant in product['variants'].values():
                    writer.writerow({'source': name, **{k: variant.get(k) for k in fields if k != 'source'}})
    # No notifications are sent until --notify is explicitly supplied.
    if reason:
        atomic(data_dir / 'outbox' / (stamp + '.json'), {'status': 'pending', 'chunks': chunks(text),
                                                       'sent_chunks': 0, 'created_at': now(), 'reason': reason})
    atomic(notice_path, notices)
    print(json.dumps({'telegram_queued_reports': report['queued_messages'], 'notification_reason': reason}), flush=True)
    print(text, flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data-de')
    parser.add_argument('--sources', nargs='+', choices=list(SOURCES), default=list(SOURCES))
    parser.add_argument('--notify', action='store_true', help='Send queued reports through Telegram')
    parser.add_argument('--telegram-check', action='store_true', help='Read-only getMe/getChat verification')
    parser.add_argument('--telegram-discover', action='store_true', help='Read-only recipient discovery after /start')
    parser.add_argument('--telegram-configure', action='store_true', help='Verify bot and save one private recipient after /start')
    parser.add_argument('--send-pending', action='store_true', help='Only send previously queued reports')
    args = parser.parse_args()
    if not monitoring_hours.is_active():
        print('Berlin 00:00–07:00: monitor paused; no fetching or Telegram sends.')
        return
    args.data_dir.mkdir(parents=True, exist_ok=True)
    if args.telegram_configure:
        telegram_configure(args.data_dir)
        return
    if args.telegram_discover:
        telegram_discover()
        return
    if args.telegram_check:
        telegram_check(args.data_dir)
        return
    if not args.send_pending:
        run(args.data_dir, args.sources)
    if args.notify or args.send_pending:
        send_pending(args.data_dir)


if __name__ == '__main__':
    try:
        main()
    except monitoring_hours.QuietHours as error:
        print(str(error))
