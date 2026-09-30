# Deploying RLECD — step by step

Copy-pasteable instructions for **Neon** (the database), **Render** (the app,
recommended) and **Vercel** (the app, with caveats you must read first).

Every command below is meant to be pasted as-is. Replace the ALL-CAPS values.

---

## 0. What you need

| Thing | Where | Notes |
|---|---|---|
| Neon account | https://console.neon.tech | Free tier is fine to start |
| Render account | https://render.com | Free plan works, but has **no shell** — see §4.4 |
| Vercel account | https://vercel.com | Only if you choose Vercel over Render |
| GitHub repo | already at `altafKhan-nep/rlecd` | `render.yaml` and `requirements.txt` are committed |
| Python 3.12 | `.python-version` | Render pins `3.12.14` |

### 0.1 Verify it runs locally first

Nothing below is worth doing until this passes on your machine.

```bash
cd /Users/altafkhan/PROJECTS/QCS/Home-Appliance
python backend/manage.py runserver 8123
```

In a second terminal:

```bash
for p in / /about/ /contact/ /kitchen-remodeling/ /sitemap.xml /robots.txt /admin/login/; do
  printf "%-24s %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8123$p)"
done
```

Every route must print `200`. Ctrl-C to stop the server.

Run the test suite too — it is the real guard on this codebase:

```bash
python backend/manage.py test
```

Expected: `Ran 587 tests` and `OK`.

---

## 1. Install the Neon skills

The Neon skills let your coding agent read and query the Neon project directly,
so you can check the deployed database without a GUI. Three pieces, install
whichever you need.

### 1.1 Everything at once (recommended)

Detects your editors, writes the MCP config, and installs the agent skills:

```bash
npx neon@latest init
```

It authenticates through OAuth in a browser and prints what it installed.

### 1.2 Agent skills only

The skills are folders of instructions your agent auto-loads when a task is
Postgres-shaped. Works with any client that supports Agent Skills, including
OpenCode:

```bash
npx skills add neondatabase/agent-skills
```

Skills you get: `neon`, `neon-postgres`, `neon-postgres-branches`,
`neon-auth`, `neon-object-storage`, `neon-functions`, `neon-ai-gateway`, and
`neon-postgres-egress-optimizer` (that last one is for diagnosing unexpected
database bills).

### 1.3 MCP server for OpenCode

If §1.1 did not write the config, add it yourself. Create `opencode.json` in the
repo root:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "neon": {
      "type": "remote",
      "url": "https://mcp.neon.tech/mcp",
      "enabled": true
    }
  }
}
```

OpenCode handles OAuth automatically. Authorise it on first use, or force it:

```bash
opencode mcp auth neon
opencode mcp list        # confirm the server is connected
```

### 1.4 Using an API key instead of OAuth

Create a key at
https://console.neon.tech/app/settings?modal=create_api_key, then:

```bash
opencode mcp logout neon   # if you already authorised via OAuth
```

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "neon": {
      "type": "remote",
      "url": "https://mcp.neon.tech/mcp",
      "enabled": true,
      "oauth": false,
      "headers": {
        "Authorization": "Bearer YOUR_NEON_API_KEY"
      }
    }
  }
}
```

Do not commit that key. Put it in your shell environment and reference it as
`{env:NEON_API_KEY}` instead if you would rather not risk it.

### 1.5 Confirm the connection

Ask your agent: *"use the neon tool to list my Neon projects"*. If it answers
with your project name, the skills and MCP server are both live.

---

## 2. Create the Neon project

1. Sign in at https://console.neon.tech
2. **Create a project** → name it `rlecd` → keep the default Postgres version.
3. Leave the region near your users (default `us-east-2` is fine).
4. Do **not** delete the `neondb` database or the default role.

### 2.1 Copy both connection strings

**Connect** in the left nav → pick branch `main`, database `neondb`, role
`neondb` owner.

You need **two** strings. Toggle **Connection pooling** on and off to get each:

| | Hostname contains | Use it for |
|---|---|---|
| **Direct** | `ep-xxx.us-east-2.aws.neon.tech` | migrations, `pg_dump`, Render |
| **Pooled** | `ep-xxx-pooler.us-east-2.aws.neon.tech` | Vercel, anything bursty |

They look like:

```
# Direct
postgresql://USER:PASSWORD@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require

# Pooled
postgresql://USER:PASSWORD@ep-xxx-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

**Which one to use, and why it matters here.** This app parses `DATABASE_URL`
itself (`backend/home_improvement/settings.py`) and forwards every parameter to
libpq untouched, so both strings work as-is. The one knob worth changing:

- **Pooled → append `&conn_max_age=0`.** Neon's pooler is PgBouncer in
  *transaction* mode: a connection is returned to the pool as soon as a
  transaction ends. Django's default here is `conn_max_age=60`, which would hold
  a pooled slot open for a minute after the last query and starve the pool under
  load. `conn_max_age=0` closes immediately.
- **Direct → leave it alone.** Two Gunicorn workers holding two connections is
  nothing, and the start-up `migrate` needs a real session.

Pooled string, ready to paste:

```
postgresql://USER:PASSWORD@ep-xxx-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require&conn_max_age=0
```

### 2.2 Keep the strings somewhere safe

Put them in your shell profile or a password manager — you will need the direct
one again for every deploy. Do not commit them.

---

## 3. Point your local machine at Neon (do this before deploying)

Catching a bad connection string now costs two minutes; catching it after a
deploy costs an hour.

```bash
cd /Users/altafkhan/PROJECTS/QCS/Home-Appliance

export DATABASE_URL='postgresql://USER:PASSWORD@ep-xxx-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require&conn_max_age=0'
export SECRET_KEY='anything-long-and-random-for-a-local-check'
export DEBUG='False'

python backend/manage.py migrate --noinput
python backend/manage.py capture_content
python backend/manage.py seed_services
python backend/manage.py seed_navigation
python backend/manage.py createsuperuser
```

Expected output:

```
pages: 19 created, 0 updated, 0 removed, 58 sections written
Service catalogue ready: 15 created, 0 updated, 15 total.
Navigation ready: 25 created, 0 updated.
```

Now the preflight check, which is the project's own deploy gate:

```bash
python scripts/preflight.py
```

It must end with `Ready to deploy` or only warnings. The three that will block
you here, and what they mean:

| Message | Fix |
|---|---|
| `CSRF_TRUSTED_ORIGINS is empty` | `export SITE_URL='https://YOUR-SITE-DOMAIN'` |
| `EMAIL_BACKEND is the console backend` | See §6 |
| `DEBUG is True` | `export DEBUG='False'` |

---

## 4. Deploy to Render (recommended)

Render runs a normal Linux container with pip and a real Gunicorn process, so
the compiled `psycopg[binary]` wheel installs cleanly and management commands
work. `render.yaml` is already committed and complete.

### 4.1 Push the code

```bash
git add -A && git commit -m "Deploy prep" && git push
```

### 4.2 Create the Blueprint

1. Render → **New** → **Blueprint**
2. Connect the `rlecd` GitHub repo. Render finds `render.yaml` automatically.
3. It shows one value it needs from you: **`DATABASE_URL`** (it is marked
   `sync: false`, so Render will not invent one).

Paste the **direct** Neon string here — not the pooled one. Render's start
command runs `migrate`, and migrations want a real session:

```
postgresql://USER:PASSWORD@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

4. **Apply**. The first build takes a few minutes.

### 4.3 What Render does for you

`render.yaml` already sets, and you do not need to touch:

```
buildCommand : check_python_version && pip install -r requirements.txt && collectstatic
startCommand : migrate --noinput && gunicorn ... --workers 2 --timeout 120
DEBUG         : False
SECRET_KEY    : generated
ALLOWED_HOSTS : *.onrender.com auto-allowed when RENDER=1
cookies       : secure, SameSite=Lax, HSTS 1 year
```

Migrations run on every start, which is safe because the free plan is a single
instance.

### 4.4 Seed the content

**The free plan has no shell**, so you cannot SSH in to run commands. Run them
from your laptop against the same Neon database — this is the same database the
app is using, so it is exactly equivalent:

```bash
cd /Users/altafkhan/PROJECTS/QCS/Home-Appliance

export DATABASE_URL='postgresql://USER:PASSWORD@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'
export SECRET_KEY='anything-long'
export DEBUG='False'
export SITE_URL='https://YOUR-SERVICE.onrender.com'

python backend/manage.py capture_content
python backend/manage.py seed_services
python backend/manage.py seed_navigation
python backend/manage.py sync_roles
python backend/manage.py createsuperuser
```

`capture_content` imports 19 pages, 58 sections and 15 services. It is
idempotent — safe to re-run. **Never** pass `--reset`; it deletes rows first.
`seed_navigation` builds the 25 header/footer items the Studio menu screens
edit; without it the navbar still works (it falls back to the service list) but
those screens would be editing rows that do not exist.

### 4.5 Verify

```bash
SITE=https://YOUR-SERVICE.onrender.com

curl -s -o /dev/null -w "home    %{http_code}\n" $SITE/
curl -s -o /dev/null -w "admin   %{http_code}\n" $SITE/admin/login/
curl -s -o /dev/null -w "sitemap %{http_code}\n" $SITE/sitemap.xml
curl -s $SITE/ | grep -c "<title>"        # must be 1
```

Then run the preflight against production from your laptop:

```bash
DATABASE_URL="$DATABASE_URL" SECRET_KEY="$SECRET_KEY" DEBUG=False \
SITE_URL="https://YOUR-SERVICE.onrender.com" python scripts/preflight.py
```

You want to see `19 published pages`, `15 services`, `25 navigation items`,
`2 staff user(s)`, and no `FAIL` lines.

### 4.6 Attach a real domain (optional)

Render → your service → **Settings** → **Custom Domains**. Then set in
**Environment**:

```
SITE_URL = https://rlecd.com
```

`SITE_URL` is what derives `CSRF_TRUSTED_ORIGINS` and the canonical/Open Graph
URLs, so set it whenever you have a real hostname. Leave `ALLOWED_HOSTS` alone —
Render's `.onrender.com` entry is added automatically and the custom domain
needs adding by hand only if you serve both.

---

## 5. Deploy to Vercel — read this first

Vercel now has zero-configuration Django support (since April 2026): it finds
`backend/manage.py`, reads `WSGI_APPLICATION`, and runs `collectstatic` for you.
The app *will* serve pages. But this project has four things Vercel cannot run,
and they are not edge cases:

| Does not run on Vercel | Consequence |
|---|---|
| `send_outbox` | **Leads are captured but the notification email is never sent.** They sit in the outbox table as `queued`. |
| `publish_due` | Pages scheduled for a future date never go live on their own. |
| `sync_roles` | The `role:*` permission groups never get created. |
| Any management command | There is no shell. Seeding must happen from your laptop. |

Those are serverless constraints, not misconfiguration: Vercel Functions run
once per request and are then destroyed, so there is no long-lived process to
host a worker, a scheduler or a beat task.

**So: use Render for this app unless you have a reason not to.** It already has
a working blueprint and no functional gaps.

If you do want Vercel — for the CDN and edge caching, or to run a second copy —
here is the honest path.

### 5.1 You do not need a `.vercelignore`

`frontend/static/img/` is 56 MB, which looks like a bundle problem but is not:
it is under the 500 MB limit, and those files **must** be in the build, because
`STATICFILES_DIRS` points at `frontend/static` and `collectstatic` copies from
there. Excluding them makes every image on the site 404.

The only thing worth ignoring is the `collectstatic` output, which Vercel
regenerates during the build anyway. If you want it:

```
# .vercelignore
backend/staticfiles/
```

That is optional. Skip the file entirely unless the build complains.

### 5.2 Create the project

1. Vercel → **Add New** → **Project** → import `rlecd`
2. Framework preset: **Django** (it is detected automatically)
3. **Deploy** — it will fail on the first pass, because there is no database yet.
   That is expected; continue to add the environment variables.

### 5.3 Environment variables

Vercel → your project → **Settings** → **Environment Variables**:

| Key | Value | Why |
|---|---|---|
| `DATABASE_URL` | Neon **pooled** string, with `&conn_max_age=0` appended | Serverless opens many short-lived connections; PgBouncer is required, not optional |
| `SECRET_KEY` | `openssl rand -base64 48` | |
| `DEBUG` | `False` | Vercel does not set `RENDER`, so the `DEBUG`+`RENDER` guard in settings will not catch this for you |
| `ALLOWED_HOSTS` | `.vercel.app,your-domain.com` | Nothing auto-allows `.vercel.app`; without this every request is a 400 |
| `CSRF_TRUSTED_ORIGINS` | `https://your-project.vercel.app` | Otherwise **every form POST is a 403** — including the contact form, so you would silently stop collecting leads |
| `SITE_URL` | `https://your-project.vercel.app` | Derives canonical and Open Graph URLs |
| `SESSION_COOKIE_SECURE` | `true` | |
| `CSRF_COOKIE_SECURE` | `true` | |
| `SECURE_SSL_REDIRECT` | `true` | |
| `SECURE_PROXY_SSL_HEADER` | leave unset | Already set in settings for Vercel's proxy |

### 5.4 Migrations and seeding

Vercel has no pre-deploy phase, so **run both from your laptop** against Neon,
before you redeploy:

```bash
export DATABASE_URL='postgresql://USER:PASSWORD@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'
export SECRET_KEY='anything-long'
export DEBUG='False'

python backend/manage.py migrate --noinput
python backend/manage.py capture_content
python backend/manage.py seed_services
python backend/manage.py seed_navigation
python backend/manage.py sync_roles
python backend/manage.py createsuperuser
```

Use the **direct** string here — migrations need a real session, and Neon's
pooler is transaction-mode.

Set a **Neon branch** up if you want preview deployments isolated; point preview
environments at a branch connection string and leave production on `main`.

### 5.5 Make the outbox and the scheduler run

This is the missing code. The usual fix is a Vercel Cron entry that hits an
authenticated endpoint, which calls the management commands:

```json
{
  "crons": [{ "path": "/internal/cron", "schedule": "*/5 * * * *" }]
}
```

That endpoint **does not exist yet**. It needs to check a bearer token, verify
the request came from Vercel Cron, and then run `publish_due` and `send_outbox`.
Say the word and I will add it.

Without it, treat Vercel as a read-only front end for content and accept that
lead notification emails stay queued.

---

## 6. Email (do this before you go live)

By default `EMAIL_BACKEND` is Django's console backend, which prints to the log
and delivers to nobody. Leads will be collected and silently never emailed.

For any real SMTP provider (Gmail, Fastmail, Resend, whatever you use):

```
EMAIL_BACKEND       = django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST          = smtp.your-provider.com
EMAIL_PORT          = 587
EMAIL_USE_TLS       = true
EMAIL_HOST_USER     = you@example.com
EMAIL_HOST_PASSWORD = your-app-password
```

Use an **app password**, not your account password. Gmail requires App Passwords
(Google Account → Security → 2-Step Verification → App Passwords) and needs
`EMAIL_HOST=smtp.gmail.com`.

Test it from your laptop against production:

```bash
DATABASE_URL="$DATABASE_URL" SECRET_KEY="$SECRET_KEY" DEBUG=False \
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend \
EMAIL_HOST=smtp.your-provider.com EMAIL_PORT=587 EMAIL_USE_TLS=True \
EMAIL_HOST_USER=you@example.com EMAIL_HOST_PASSWORD=your-app-password \
python backend/manage.py send_outbox
```

`1 sent, 0 failed.` means it works. Then submit the contact form on the live
site and confirm the row appears in Studio → Leads and an `EmailOutbox` row is
created.

---

## 7. Keep the scheduled jobs running

`publish_due` and `send_outbox` need to run on a timer. On Render, add a cron
service to `render.yaml`:

```yaml
  - type: cron
    name: rlecd-cron
    schedule: "*/5 * * * *"
    buildCommand: pip install -r requirements.txt
    startCommand: python backend/manage.py publish_due && python backend/manage.py send_outbox
    envVars:
      - key: DATABASE_URL
        fromService: { type: web, name: rlecd, property: DATABASE_URL }
      - key: SECRET_KEY
        fromService: { type: web, name: rlecd, property: SECRET_KEY }
```

Cron jobs on Render are billed as web services. On the free plan, use
https://cron-job.org to hit a Render cron service, or run them from your own
scheduler.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every page is a 400 | `ALLOWED_HOSTS` missing your hostname | Add it. Vercel needs `.vercel.app` explicitly |
| Page loads, contact form 403s | `CSRF_TRUSTED_ORIGINS` empty | Set `SITE_URL` or `CSRF_TRUSTED_ORIGINS` |
| `no such table: content_page` | Migrations never ran | Run `migrate` from your laptop against Neon |
| 404s everywhere, admin works | Content not seeded | Run `capture_content`, `seed_services`, `seed_navigation` |
| Empty Services dropdown | `seed_navigation` never ran | Run it; the navbar falls back to services until then |
| Leads arrive, no email arrives | Console email backend | §6 |
| `too many connections` on Vercel | Direct Neon string | Use the **pooled** string with `conn_max_age=0` |
| `ImportError: no pq wrapper available` | `psycopg` wheel mismatch | Pin `PYTHON_VERSION` to match `.python-version` |
| Changes not showing after deploy | Stale cache | The cache is the database, invalidated on write; redeploy if you edited templates |
| `Cache table 'django_cache' already exists` during tests | Cosmetic — migration creates it, then Django's test bootstrap notices | Ignore |
| Render deploy fails on Python version | `.python-version` is `3.12`, Render wants exact | Set `PYTHON_VERSION=3.12.14` in environment variables |

---

## 9. Everyday commands

```bash
# Tests
python backend/manage.py test

# Deploy gate — run before every deploy
python scripts/preflight.py

# Mirror check: proves the DB reproduces the captured site
python backend/manage.py test content.tests.MirrorRegressionTests

# Re-import content after editing captured templates (never --reset)
python backend/manage.py capture_content

# Content commands
python backend/manage.py sync_roles
python backend/manage.py seed_navigation --prune     # delete items removed from the seed
python backend/manage.py seed_navigation --rebuild   # discard all menu edits

# Scheduled jobs
python backend/manage.py publish_due
python backend/manage.py send_outbox
```
