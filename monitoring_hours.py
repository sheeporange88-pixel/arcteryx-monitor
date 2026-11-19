"""Berlin business-hour gate, including CET/CEST changes and delayed CI runs."""
from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo('Europe/Berlin')


class QuietHours(RuntimeError):
    pass


def is_active(instant=None):
    instant = instant if instant is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError('Monitoring time must be timezone-aware')
    return instant.astimezone(BERLIN).hour >= 7


def ensure_active():
    if not is_active():
        raise QuietHours('Berlin 00:00–07:00: product requests and Telegram sends paused.')


def main():
    local = datetime.now(timezone.utc).astimezone(BERLIN)
    active = is_active(local)
    target = os.environ.get('GITHUB_OUTPUT')
    if target:
        with open(target, 'a') as stream:
            stream.write('allowed=' + str(active).lower() + '\n')
    print(local.strftime('%Y-%m-%d %H:%M %Z') + ': ' +
          ('monitoring enabled' if active else 'quiet hours; skip fetching and Telegram'))


if __name__ == '__main__':
    main()
