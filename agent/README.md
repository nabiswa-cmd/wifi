# NABISWA WIFI — on-site agent

This is the piece that actually talks to your MikroTik RB941. It does NOT
run on Railway — it runs on a small always-on machine on the same LAN as
the router (a Raspberry Pi, an old laptop, a mini PC — anything that stays
powered on and can reach the router's IP).

## Why a separate agent at all?

Railway (like Vercel) runs your Django app in the cloud. Your RB941 is on
a local network behind your ISP's NAT/router, with no public IP. Cloud
Django can't open a socket directly to it. So instead:

1. Django writes a row to `MikroTikJob` whenever it needs the router to
   do something ("create this hotspot user", "disconnect that one").
2. This agent, sitting on the LAN, polls Django every few seconds for
   pending jobs, executes them against the router over the RouterOS API,
   and reports back.
3. It also sends a heartbeat every cycle, with a snapshot of who's
   currently online — this is what powers "is the router connected"
   status in the dashboard.

## 1. On the RB941 itself

Make sure the API service is enabled (it usually is by default):
`IP > Services` — confirm `api` (port 8728) is enabled, or `api-ssl`
(port 8729) if you want it encrypted. Create a dedicated API user rather
than using the main `admin` account:

```
/user add name=agent password=<strong-password> group=full
```

Also make sure a HotSpot is already set up on the router (`IP > Hotspot >
Hotspot Setup` wizard) — the agent manages *users* inside that hotspot
server, it doesn't create the hotspot server itself.

## 2. On the on-site machine

```bash
git clone <your-repo-url> nabiswa-wifi
cd nabiswa-wifi/agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env   # fill in MIKROTIK_HOST/USERNAME/PASSWORD, DJANGO_BASE_URL, MIKROTIK_AGENT_API_KEY
python agent.py
```

You should see `Connected to router.` in the log. Leave it running — Ctrl+C
stops it. For a machine that reboots on its own (power cuts are normal),
install it as a systemd service so it survives reboots:

```bash
sudo useradd -r -s /bin/false nabiswa    # if it doesn't exist yet
sudo mkdir -p /opt/nabiswa-wifi
sudo cp -r ~/nabiswa-wifi/* /opt/nabiswa-wifi/
sudo chown -R nabiswa:nabiswa /opt/nabiswa-wifi
sudo cp /opt/nabiswa-wifi/agent/nabiswa-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nabiswa-agent
sudo journalctl -u nabiswa-agent -f   # watch logs
```

## 3. On Django (Railway)

Set the matching environment variable so the two sides trust each other:

```
MIKROTIK_AGENT_API_KEY=<same long random string as the agent's .env>
```

Then, in the Django admin (`/django-admin/`), create a `MikroTikRouter`
row with `is_active=True` and the same host/username/password you gave
the agent (Django doesn't call the router directly, but this record is
what associates jobs and heartbeats with "the" active router — Section 13
in the project's design notes).

## Troubleshooting

- **Dashboard says "no heartbeat received from the on-site agent yet"** —
  the agent isn't running, can't reach `DJANGO_BASE_URL`, or the API key
  doesn't match on both sides.
- **Agent logs "Router connection problem"** — check `MIKROTIK_HOST` is
  reachable from the agent machine (`ping 192.168.88.1`), that the `api`
  service is enabled on the router, and the port/credentials are right.
- **Jobs stay PENDING** — the agent polls `/api/mikrotik/jobs/pending/`
  only for the router marked `is_active=True`; confirm there's exactly
  one such row and it matches the router the agent is pointed at.
