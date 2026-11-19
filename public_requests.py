"""Pace public site requests and stop a run when the server refuses access."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time
from urllib.parse import urlsplit

HOSTS = {'arcteryx.com', 'outlet.arcteryx.com'}
USER_AGENT = 'ArcteryxPriceMonitor/1.0 (+https://github.com/sheeporange88-pixel/arcteryx-monitor)'


class SitePaused(RuntimeError):
    pass


class PublicRequests:
    def __init__(self, interval=0.5):
        self.interval = interval
        self.lock = threading.Lock()
        self.next_request = {}
        self.blocked = {}
        self.diagnostics = []
        self.cooldowns = {}

    def before(self, url):
        host = urlsplit(url).hostname
        if host not in HOSTS:
            return
        with self.lock:
            if self.cooldowns.get(host, 0) > time.time():
                raise SitePaused('服务器要求稍后访问，本轮暂停此站请求（Retry-After）')
            if host in self.blocked:
                raise SitePaused('本轮暂停此站后续请求（HTTP ' + str(self.blocked[host]) + '）')
            delay = max(0, self.next_request.get(host, 0) - time.monotonic())
            # Serialize request starts, including queued worker threads.
            if delay:
                time.sleep(delay)
            self.next_request[host] = time.monotonic() + self.interval

    def failure(self, url, error):
        parts = urlsplit(url)
        if parts.hostname not in HOSTS:
            return 'HTTP ' + str(error.code)
        allowed_headers = ('Content-Type', 'Server', 'Retry-After')
        headers = {key.lower(): str((error.headers or {}).get(key, ''))[:150] for key in allowed_headers}
        # Record only classifications, never cookies or full response bodies.
        try:
            snippet = error.read(4096).decode('utf-8', 'replace').lower()
        except Exception:
            snippet = ''
        indicators = [word for word in ('captcha', 'access denied', 'not acceptable', 'rate limit') if word in snippet]
        diagnostic = {'host': parts.hostname, 'path': parts.path, 'status': error.code,
                      'headers': headers, 'response_indicators': indicators}
        with self.lock:
            if len(self.diagnostics) < 10:
                self.diagnostics.append(diagnostic)
            if error.code in (403, 406, 429):
                self.blocked[parts.hostname] = error.code
            retry_after = headers['retry-after']
            if retry_after:
                try:
                    until = time.time() + max(0, float(retry_after))
                except ValueError:
                    try:
                        until = parsedate_to_datetime(retry_after).timestamp()
                    except (ValueError, TypeError, OverflowError):
                        until = 0
                if until:
                    self.cooldowns[parts.hostname] = until
        return 'HTTP ' + str(error.code)

    def restore(self, record):
        self.cooldowns = {host: value for host, value in record.items()
                          if host in HOSTS and isinstance(value, (int, float)) and value > time.time()}

    def snapshot(self):
        return {'requests_per_second_per_host': 1 / self.interval,
                'paused_hosts': self.blocked.copy(), 'diagnostics': list(self.diagnostics),
                'retry_after_until': {host: datetime.fromtimestamp(value, timezone.utc).isoformat()
                                      for host, value in self.cooldowns.items() if value > time.time()}}
