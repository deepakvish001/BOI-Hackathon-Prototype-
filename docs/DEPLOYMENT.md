# Deployment

Everything below was measured on this repository at the commit it ships with,
on a 4-core CPU container with no GPU. Where a number is an estimate rather than
a measurement, it says so.

---

## What you are deploying

One process. `uvicorn bodhi.api.main:app` serves both:

| Path | Audience |
|---|---|
| `/api/*` | the machine interface a core banking system calls |
| `/` | the investigator dashboard (static files from `dashboard/`) |

There is no separate frontend build, no database, no message broker and no
model server. The engine has no GPU, CUDA or deep-learning-framework
dependency — GraphSAGE and the temporal network are NumPy — so the image is a
plain `python:3.11-slim` and starts anywhere.

State lives on disk under `$BODHI_ARTIFACTS` (default `./artifacts`):

```
artifacts/data/      simulated bank tables      (parquet)
artifacts/models/    trained layers             (.ubj / .joblib / .npz)
artifacts/runtime/   audit log, casebook        (append-only jsonl)
artifacts/metrics/   evaluation.json            (what the report cites)
```

---

## Resource requirements — measured

The engine holds the whole population in memory to score the graph, so RAM
scales with the size of the simulated bank, not with request volume.

| Baked world | Boot time | Peak RSS | `artifacts/data` |
|---|---|---|---|
| 6,000 accounts / 90 days *(what the Dockerfile builds)* | **23 s** | **1.13 GB** | 12 MB |
| 12,000 accounts / 120 days *(what `make all` builds)* | **85 s** | **3.09 GB** | 30 MB |

Build-time cost, same machine: `generate_data.py` 5 s, `train.py` 62 s.

**Provision at least 2 GB RAM for the 6,000-account world, 4 GB for the
12,000-account one.** A 512 MB free tier will be OOM-killed during boot — this
is the single most common way this deployment fails.

CPU is only needed at boot and during a re-score; inline scoring is 0.054 ms at
the median, so one vCPU serves a demo comfortably.

---

## The one behaviour you must design around

`bodhi/api/state.py` bootstraps **lazily** — the world is loaded by the first
request that touches `get_state()`, not at process start. So:

- the container binds port 8000 immediately, but
- the **first HTTP request blocks for the full boot time**, and
- `/api/health` is one of those requests.

Measured on the 12,000-account world: the first `curl /api/health` returned
after **73 s**; the second returned in **11 ms**.

Consequences:

1. Every platform health check needs a **start period / initial delay longer
   than the boot time**. The bundled `HEALTHCHECK` already uses
   `--start-period=90s`; a PaaS health check with a 30 s timeout and no grace
   period will kill the container in a restart loop.
2. **Warm it yourself after deploy** so the first human visitor is not the one
   who waits:
   ```bash
   curl -fsS --max-time 180 https://<your-host>/api/health
   ```
3. Run **one worker**. `--workers 4` builds the world four times, in four
   separate memory spaces. There is no shared state between workers, so alerts
   raised against one are invisible to the others.

If you would rather the process warm itself in the background and answer health
checks instantly, that is a change to `get_state()` — start `bootstrap()` on a
thread at startup and let `/api/health` report `status: "warming"` until it
finishes. It is not wired that way today.

---

## Option A — Docker (recommended)

The image bakes the simulated bank and the trained models at build time, so the
container starts ready to serve instead of training on first request.

```bash
docker build -t bodhi-mule-hunter .
docker run -d --name bodhi -p 8000:8000 --memory=2g bodhi-mule-hunter
```

Then verify — in this order, because the first call is the slow one:

```bash
curl -fsS --max-time 180 localhost:8000/api/health   # blocks ~23 s the first time
curl -fsS localhost:8000/api/overview | head -c 400
open http://localhost:8000                            # the dashboard
```

`/api/health` returns `status`, `models_loaded`, `accounts_in_graph`,
`transactions_seen` and `open_alerts`. The bootstrap mode is reported by
`/api/overview` instead: a healthy container shows
`bootstrap_mode: "artifacts+loaded-models"`. If it says `generated-demo` the
baked artefacts were not found and it built a smaller world on the fly — the
service works, but it is not the world you trained.

To keep the audit log and casebook across container restarts, mount a volume:

```bash
docker run -d -p 8000:8000 --memory=2g \
  -v bodhi-runtime:/app/artifacts/runtime \
  bodhi-mule-hunter
```

To change the baked world size, edit the `RUN python scripts/generate_data.py`
line in the `Dockerfile` — and re-check the RAM table above before you raise it.

**Note:** the image could not be built in the environment this guide was written
in (no Docker daemon), so the build is reviewed rather than executed. The
`Dockerfile` steps were each verified individually: the script flags exist, the
dependencies resolve, and the boot numbers above come from running the same
generate + train + load sequence directly.

---

## Option B — a plain VM (Ubuntu 22.04+)

This is what a bank would actually do: no container runtime, systemd owns the
process, nginx terminates TLS.

```bash
# 1. system packages
sudo apt update && sudo apt install -y python3.11 python3.11-venv git nginx

# 2. code and dependencies
sudo useradd -m -s /bin/bash bodhi
sudo -u bodhi -i
git clone https://github.com/archirajpoot/Bodhi_Mule_Hunter.git app && cd app
make setup                    # creates .venv, installs requirements.txt

# 3. build the world and train (once; ~70 s)
make all                      # data + train + sample-apk + evaluate
exit
```

`/etc/systemd/system/bodhi.service`:

```ini
[Unit]
Description=BODHI MULE HUNTER AI
After=network-online.target

[Service]
User=bodhi
WorkingDirectory=/home/bodhi/app
Environment=BODHI_ARTIFACTS=/home/bodhi/app/artifacts
ExecStart=/home/bodhi/app/.venv/bin/uvicorn bodhi.api.main:app \
          --host 127.0.0.1 --port 8000 --workers 1 --timeout-keep-alive 65
Restart=always
RestartSec=5
# The process holds the population in memory; see the RAM table.
MemoryMax=4G

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now bodhi
sudo systemctl status bodhi
curl -fsS --max-time 180 localhost:8000/api/health     # warm it
```

`/etc/nginx/sites-available/bodhi`:

```nginx
server {
    listen 80;
    server_name your-domain.example;

    # The first request after a restart takes up to 90 s; the default 60 s
    # proxy read timeout would return 504 instead of waiting for it.
    proxy_read_timeout 180s;
    client_max_body_size 32m;          # APK uploads to /api/shield/analyze

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/bodhi /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example        # TLS
```

---

## Option C — a managed platform (Render / Railway / Fly.io)

All three can build the `Dockerfile` directly. The settings that matter are the
same everywhere:

| Setting | Value | Why |
|---|---|---|
| Build | Dockerfile | the baked artefacts are the point |
| Instance RAM | **≥ 2 GB** | 512 MB / 1 GB is OOM-killed at boot |
| Port | `8000` | or read `$PORT` — see below |
| Health check path | `/api/health` | |
| Health check grace period | **≥ 120 s** | the first request is the slow one |
| Workers | 1 | four workers build four worlds |
| Disk | ephemeral is fine for a demo | the audit log is lost on restart |

Some platforms inject a `$PORT` rather than letting you choose. Override the
command instead of editing the `Dockerfile`:

```
uvicorn bodhi.api.main:app --host 0.0.0.0 --port $PORT --workers 1
```

**Fly.io** additionally wants a `fly.toml`; the machine size must be `shared-cpu-1x`
with at least 2048 MB, and set

```toml
[[services.http_checks]]
  path = "/api/health"
  grace_period = "120s"
  timeout = "10s"
```

Free tiers on all three sleep idle instances. A sleeping instance re-boots the
whole world on the next request, so a judge clicking your link cold waits the
full boot time. If the demo is being judged live, keep it warm with a cron ping
every few minutes, or use Option D.

---

## Option D — a tunnel, for live judging

The fastest reliable way to show a running system without any hosting account.
Start the server locally, expose it, keep the laptop open:

```bash
make serve                                     # binds 0.0.0.0:8000
curl -fsS --max-time 180 localhost:8000/api/health   # warm it before demoing

# in another terminal
cloudflared tunnel --url http://localhost:8000       # prints an https URL
# or: ngrok http 8000
```

The dashboard is same-origin with the API, so no CORS configuration is needed
through a tunnel. Warm it *before* you share the link.

---

---

## Why not Vercel (or Netlify, or Cloudflare Workers)

Asked often enough to answer here. **The engine cannot run on Vercel.** Not a
configuration problem — four hard limits, each independently fatal:

**1. Bundle size.** A Vercel serverless function is capped at **250 MB
unzipped**. Measured, from this project's own virtualenv:

| Package | Unzipped |
|---|---|
| xgboost | **228 MB** |
| pyarrow | 156 MB |
| scipy | 113 MB |
| pandas | 76 MB |
| scikit-learn | 50 MB |
| numpy | 45 MB |
| matplotlib | 36 MB |
| networkx | 19 MB |

`xgboost` alone nearly fills the budget; the runtime set is roughly 720 MB.

**2. Memory.** Boot peaks at 1.13 GB for the small world and 3.09 GB for the
large one. Vercel functions top out around 3 GB on paid plans and default to
far less.

**3. Execution time.** Every cold invocation rebuilds the whole world, because
there is no process to keep it in. That is 23–73 s of work against a function
timeout measured in tens of seconds. Even where the timeout allows it, *every*
cold request pays it.

**4. State.** The filesystem is read-only apart from an ephemeral `/tmp`, and
each invocation is a fresh process. The append-only audit log, the casebook and
the kill-switch rate limiter all assume a long-lived process with a writable
directory. Alerts raised in one invocation would not exist in the next.

Vercel is a platform for stateless request handlers and static frontends. This
is a stateful, memory-resident analytics engine. It needs a container or a VM —
Options A, B and C above.

### What *can* go on Vercel: the dashboard only

`dashboard/` is plain HTML, CSS and one JS file — a legitimate static deploy,
with the API hosted on Render, Fly or a VM. Two changes are needed first, and
neither is wired today:

1. **An API base URL.** `dashboard/app.js` calls `fetch('/api/…')` in 15 places
   through one `api()` helper, so a single configurable `API_BASE` prepended
   inside that helper covers all of them — plus the two direct `fetch()` calls
   for the STR text and the sample APK.
2. **CORS on the server.** There is no `CORSMiddleware` in `bodhi/api/main.py`,
   because today the dashboard is same-origin with the API. A browser on
   `*.vercel.app` calling a different host will be blocked until the API allows
   that origin explicitly.

Unless a `vercel.app` URL is specifically wanted, this split is strictly worse
than serving both from one container: two deploys to keep in step, a CORS
surface that did not need to exist, and no benefit — the dashboard is a handful
of static files that any of the other options already serves.

## Before you expose it publicly

**There is no authentication on any endpoint.** That is deliberate for a
prototype judged by clicking, and unacceptable on a public URL, because these
routes are open to anyone who finds it:

| Route | What an anonymous caller can do |
|---|---|
| `POST /api/actions/killswitch` | ask for an account to be frozen |
| `POST /api/actions/revert` | undo a containment action |
| `POST /api/alerts/{id}/resolve` | close an alert |
| `POST /api/ingest` | inject transactions into the graph |
| `POST /api/shield/analyze` | upload an arbitrary file for APK triage |
| `GET /api/audit` | read the audit log |

The kill-switch cannot actually freeze anything — it writes a decision to the
audit log — so the blast radius is a corrupted demo, not a frozen customer. It
is still a public write endpoint.

Cheapest adequate fix for a demo, one line of nginx:

```bash
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd judge
```

```nginx
location / {
    auth_basic "BODHI";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://127.0.0.1:8000;
    # ... headers as above
}
```

For anything beyond a demo, the API needs a real dependency-injected API key or
OIDC on `/api/*`, and the write routes need an actor identity that comes from
the token rather than from a `?actor=` query parameter.

Two more things a real deployment needs that this prototype does not implement:
the audit log is hash-chained but its head hash is not anchored anywhere
external, and the runtime writes plain files with no encryption at rest.

---

## Verifying a deployment

```bash
BASE=https://your-host

curl -fsS --max-time 180 $BASE/api/health          # bootstrap_mode, models_loaded
curl -fsS $BASE/api/overview                       # population, alerts, exposure
curl -fsS $BASE/api/metrics                        # what the report cites
curl -fsS "$BASE/api/alerts?limit=3"               # top alerts
curl -fsS -X POST "$BASE/api/score/transaction" \
     -H 'content-type: application/json' \
     -d '{"transaction":{"txn_id":"T1","timestamp":"2026-08-17T03:12:00",
          "src_account":"AC000001","dst_account":"AC000002",
          "amount":48000,"channel":"UPI"}}'
```

A deployment is good when `/api/health` reports `status: "ok"`,
`models_loaded: true` and an `accounts_in_graph` equal to the world you baked,
and `/api/overview` reports `bootstrap_mode: "artifacts+loaded-models"`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Container exits 137 during boot | OOM | raise the memory limit; see the RAM table |
| Platform restarts the container in a loop | health check fired before boot finished | grace period ≥ 120 s |
| First page load takes a minute, then everything is fast | lazy bootstrap | warm with `curl /api/health` after deploy |
| `502` / `504` through nginx on the first request | `proxy_read_timeout` too low | set it to 180 s |
| `/api/overview` shows `bootstrap_mode: "generated-demo"` | `artifacts/data` not found in the image | check `BODHI_ARTIFACTS` and that the build ran `generate_data.py` |
| Alerts appear and then vanish | several workers, each with its own world | `--workers 1` |
| Audit log empty after a restart | `artifacts/runtime` is ephemeral | mount a volume |
| `ModuleNotFoundError: openpyxl` running `boi_train.py` | dependency missing | it is declared in `requirements.txt`; reinstall |

---

## What this deployment is not

It serves a **simulated** bank. Pointing it at real data means replacing
`bodhi/data/generator.py` with a connector to the bank's warehouse, retraining
on labelled cases, and re-measuring everything in
`artifacts/metrics/evaluation.json` — the model numbers in the report are for
simulated data and do not transfer. The architecture, the API surface and the
containment constraints do.
