#!/usr/bin/env python3
"""
NABISWA WIFI on-site agent.

Runs on a small always-on machine on the SAME LAN as the MikroTik router
(a Raspberry Pi, an old laptop, a $5 VPS with a WireGuard tunnel into the
LAN — anything that can reach the router's API port). Django itself never
opens a socket to the router (it's on Railway/Vercel, the router is
behind NAT) — this script is the bridge:

  1. Polls  GET  /api/mikrotik/jobs/pending/   for queued work
  2. Executes each job against the router over the RouterOS API
     (port 8728 plaintext, or 8729 if MIKROTIK_USE_SSL=true)
  3. Reports the result back with POST /api/mikrotik/jobs/<id>/complete/
  4. Sends a heartbeat (with a live user/session snapshot) every cycle to
     POST /api/mikrotik/heartbeat/  so Django knows the agent — and by
     extension the router — is actually reachable (Section 36: never
     assume connected just because nothing has failed yet).

Config comes entirely from environment variables — copy .env.example to
.env and fill it in, or export the variables however your process
supervisor wants.

Requires: pip install -r agent/requirements.txt (librouteros + requests).
This file intentionally has ZERO Django imports — it must be runnable
on a bare Python 3 install on whatever box you point at the router.
"""
import json
import logging
import os
import sys
import time

import requests
from librouteros import connect as ros_connect
from librouteros.exceptions import LibRouterosError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # fine if python-dotenv isn't installed; env vars can be set another way

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
log = logging.getLogger('nabiswa-agent')

# --- Config -----------------------------------------------------------
ROUTER_HOST = os.environ['MIKROTIK_HOST']                    # e.g. 192.168.88.1
ROUTER_PORT = int(os.environ.get('MIKROTIK_PORT', '8728'))   # 8729 if MIKROTIK_USE_SSL=true
ROUTER_USER = os.environ['MIKROTIK_USERNAME']
ROUTER_PASSWORD = os.environ['MIKROTIK_PASSWORD']
ROUTER_USE_SSL = os.environ.get('MIKROTIK_USE_SSL', 'false').lower() == 'true'

DJANGO_BASE_URL = os.environ['DJANGO_BASE_URL'].rstrip('/')  # e.g. https://your-app.up.railway.app
AGENT_API_KEY = os.environ['MIKROTIK_AGENT_API_KEY']         # must match Django's setting of the same name

POLL_INTERVAL = float(os.environ.get('AGENT_POLL_INTERVAL', '5'))
HTTP_TIMEOUT = float(os.environ.get('AGENT_HTTP_TIMEOUT', '10'))

HEADERS = {'Authorization': f'Bearer {AGENT_API_KEY}', 'Content-Type': 'application/json'}


# --- RouterOS helpers ---------------------------------------------------
def connect_router():
    kwargs = {
        'host': ROUTER_HOST,
        'username': ROUTER_USER,
        'password': ROUTER_PASSWORD,
        'port': ROUTER_PORT,
    }
    if ROUTER_USE_SSL:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs['ssl_wrapper'] = ctx.wrap_socket
    return ros_connect(**kwargs)


def _hotspot_users(api):
    return api.path('ip', 'hotspot', 'user')


def _hotspot_active(api):
    return api.path('ip', 'hotspot', 'active')


def _find_user_id(api, username):
    for row in _hotspot_users(api):
        if row.get('name') == username:
            return row.get('.id')
    return None


def _find_active_ids(api, username):
    return [row.get('.id') for row in _hotspot_active(api) if row.get('user') == username]


# --- Job execution -------------------------------------------------------
def run_job(api, job):
    job_type = job['job_type']
    payload = job['payload']
    users = _hotspot_users(api)
    active = _hotspot_active(api)

    if job_type == 'CREATE_USER':
        existing = _find_user_id(api, payload['username'])
        if existing:
            users.update(**{'.id': existing, 'password': payload['password'],
                             'profile': payload['profile_name'], 'disabled': 'no'})
        else:
            users.add(name=payload['username'], password=payload['password'],
                      profile=payload['profile_name'])
        return 'created/updated'

    if job_type == 'DISCONNECT_USER':
        ids = _find_active_ids(api, payload['username'])
        for aid in ids:
            active.remove(aid)
        return f'disconnected {len(ids)} active session(s)'

    if job_type == 'DISABLE_USER':
        uid = _find_user_id(api, payload['username'])
        if not uid:
            raise LookupError(f"no hotspot user named {payload['username']!r}")
        users.update(**{'.id': uid, 'disabled': 'yes'})
        for aid in _find_active_ids(api, payload['username']):
            active.remove(aid)
        return 'disabled'

    if job_type == 'ACTIVATE_USER':
        uid = _find_user_id(api, payload['username'])
        if not uid:
            raise LookupError(f"no hotspot user named {payload['username']!r}")
        users.update(**{'.id': uid, 'disabled': 'no'})
        return 'activated'

    if job_type == 'DELETE_USER':
        uid = _find_user_id(api, payload['username'])
        if uid:
            users.remove(uid)
        for aid in _find_active_ids(api, payload['username']):
            active.remove(aid)
        return 'deleted'

    if job_type == 'UPDATE_USER':
        uid = _find_user_id(api, payload['username'])
        if not uid:
            raise LookupError(f"no hotspot user named {payload['username']!r}")
        fields = {k: str(v) for k, v in payload.get('fields', {}).items()}
        users.update(**{'.id': uid, **fields})
        return 'updated'

    if job_type == 'SET_BANDWIDTH':
        uid = _find_user_id(api, payload['username'])
        if not uid:
            raise LookupError(f"no hotspot user named {payload['username']!r}")
        users.update(**{'.id': uid, 'limit-bytes-total': '0', 'rate-limit': payload['rate_limit']})
        return 'bandwidth set'

    if job_type == 'BYPASS_MAC':
        bindings = api.path('ip', 'hotspot', 'ip-binding')
        mac = payload['mac_address']
        existing = next((r for r in bindings if r.get('mac-address') == mac), None)
        if existing:
            bindings.update(**{'.id': existing['.id'], 'type': 'bypassed',
                                'comment': payload.get('comment', '')})
        else:
            bindings.add(**{'mac-address': mac, 'type': 'bypassed',
                             'comment': payload.get('comment', '')})
        return f'{mac} bypassed — online immediately'

    if job_type == 'UNBYPASS_MAC':
        bindings = api.path('ip', 'hotspot', 'ip-binding')
        mac = payload['mac_address']
        removed = 0
        for row in list(bindings):
            if row.get('mac-address') == mac:
                bindings.remove(row['.id'])
                removed += 1
        return f'removed {removed} binding(s) for {mac}'

    if job_type == 'SET_SESSION_TIMEOUT':
        uid = _find_user_id(api, payload['username'])
        if not uid:
            raise LookupError(f"no hotspot user named {payload['username']!r}")
        users.update(**{'.id': uid, 'session-timeout': payload['timeout']})
        return 'session timeout set'

    raise ValueError(f'unknown job_type {job_type!r}')


# --- Django HTTP helpers --------------------------------------------------
def fetch_pending_jobs():
    resp = requests.get(f'{DJANGO_BASE_URL}/api/mikrotik/jobs/pending/',
                         headers=HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get('jobs', [])


def report_job(job_id, status, detail=''):
    requests.post(f'{DJANGO_BASE_URL}/api/mikrotik/jobs/{job_id}/complete/',
                   headers=HEADERS, timeout=HTTP_TIMEOUT,
                   data=json.dumps({'status': status, 'detail': detail}))


def send_heartbeat(api):
    active_users, active_sessions = [], []
    try:
        active_users = [
            {'username': r.get('name'), 'disabled': r.get('disabled')}
            for r in _hotspot_users(api)
        ]
        active_sessions = [
            {
                'username': r.get('user'),
                'address': r.get('address'),
                'mac_address': r.get('mac-address'),
                'uptime': r.get('uptime'),
                'bytes_in': r.get('bytes-in'),
                'bytes_out': r.get('bytes-out'),
            }
            for r in _hotspot_active(api)
        ]
    except LibRouterosError as exc:
        log.warning('Could not read router state for heartbeat snapshot: %s', exc)

    try:
        requests.post(
            f'{DJANGO_BASE_URL}/api/mikrotik/heartbeat/',
            headers=HEADERS, timeout=HTTP_TIMEOUT,
            data=json.dumps({'active_users': active_users, 'active_sessions': active_sessions}),
        )
    except requests.RequestException as exc:
        log.warning('Heartbeat POST to Django failed: %s', exc)


# --- Main loop -------------------------------------------------------------
def main():
    log.info('Starting NABISWA WIFI agent — router %s:%s, django %s',
              ROUTER_HOST, ROUTER_PORT, DJANGO_BASE_URL)
    api = None
    while True:
        try:
            if api is None:
                api = connect_router()
                log.info('Connected to router.')

            send_heartbeat(api)

            for job in fetch_pending_jobs():
                job_id = job['id']
                try:
                    detail = run_job(api, job)
                    report_job(job_id, 'DONE', detail)
                    log.info('Job %s (%s): %s', job_id, job['job_type'], detail)
                except (LibRouterosError, LookupError, ValueError) as exc:
                    report_job(job_id, 'FAILED', str(exc))
                    log.error('Job %s (%s) failed: %s', job_id, job['job_type'], exc)

        except (LibRouterosError, OSError) as exc:
            log.error('Router connection problem, will retry: %s', exc)
            api = None
        except requests.RequestException as exc:
            log.error('Could not reach Django, will retry: %s', exc)
        except KeyboardInterrupt:
            log.info('Shutting down.')
            sys.exit(0)

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
