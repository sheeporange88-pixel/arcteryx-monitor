"""Keep ten-minute checks; notify on changes and on the hourly :55 check."""
from datetime import datetime, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo


def hourly_slot(started_at):
    local = datetime.fromisoformat(started_at).astimezone(ZoneInfo('Europe/Berlin'))
    if local.minute < 5:
        slot = (local - timedelta(hours=1)).replace(minute=55, second=0, microsecond=0)
    else:
        slot = local.replace(minute=5 + 10 * ((local.minute - 5) // 10), second=0, microsecond=0)
    if slot.minute == 55 and slot.hour >= 7:
        return slot.isoformat()
    return None


def fingerprint(value):
    def clean(item):
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items()
                    if k not in ('checked_at', 'detected_at', 'first_observed_at')}
        if isinstance(item, list):
            return [clean(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def decide(state, key, started_at, *, changed=False, initial=False, status_changed=False):
    """Mutate durable queue state only when a new notification should be queued."""
    watch = state.setdefault('watches', {}).setdefault(key, {})
    slot = hourly_slot(started_at)
    heartbeat = slot is not None and watch.get('hourly_slot') != slot
    reason = ('change' if changed else 'status_change' if status_changed else
              'initial_baseline' if initial else 'hourly_55' if heartbeat else None)
    if reason and slot:
        watch['hourly_slot'] = slot
    return reason


def price_changed(previous, current):
    if not previous or not current:
        return False
    old, new = previous['variants'], current['variants']
    fields = ('list_price', 'current_price')
    prices = lambda v: tuple(v[k] for k in fields)
    return (any(prices(old[vid]) != prices(v) for vid, v in new.items() if vid in old)
            or {prices(v) for v in old.values()} != {prices(v) for v in new.values()})


def report_errors(report):
    return fingerprint({
        'sources': {name: sorted(source.get('errors', []), key=lambda e: json.dumps(e, sort_keys=True))
                    for name, source in report.get('sources', {}).items()},
        'promotions': {name: p.get('error') for name, p in report.get('promotions', {}).items()},
    })
