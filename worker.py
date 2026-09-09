#!/usr/bin/env python3
"""
The "one always-on process" the README flags as the thing Vercel/Railway's
request-driven web dyno can't do on its own. Run this as a SEPARATE
Railway service (see Procfile's `worker:` line) alongside the `web:`
service — same codebase, same DATABASE_URL, different process type.

Loops forever, calling the two management commands on an interval:
  - expire_subscriptions  (every EXPIRY_INTERVAL_SECONDS)
  - sync_mikrotik         (every SYNC_INTERVAL_SECONDS)
"""
import os
import time

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.management import call_command  # noqa: E402

EXPIRY_INTERVAL_SECONDS = int(os.environ.get('EXPIRY_INTERVAL_SECONDS', '60'))
SYNC_INTERVAL_SECONDS = int(os.environ.get('SYNC_INTERVAL_SECONDS', '30'))


def main():
    last_expiry = 0.0
    last_sync = 0.0
    print('Worker started.', flush=True)
    while True:
        now = time.monotonic()
        if now - last_expiry >= EXPIRY_INTERVAL_SECONDS:
            try:
                call_command('expire_subscriptions')
            except Exception as exc:  # noqa: BLE001 - keep the loop alive no matter what
                print(f'expire_subscriptions failed: {exc}', flush=True)
            last_expiry = now

        if now - last_sync >= SYNC_INTERVAL_SECONDS:
            try:
                call_command('sync_mikrotik')
            except Exception as exc:  # noqa: BLE001
                print(f'sync_mikrotik failed: {exc}', flush=True)
            last_sync = now

        time.sleep(5)


if __name__ == '__main__':
    main()
