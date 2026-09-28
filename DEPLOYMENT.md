# Deploying to Vercel

## What changed and why

Serverless has no persistent filesystem and no long-lived process, so two
things that work locally cannot work in production:

| Local assumption | Deployment reality | Fix |
| --- | --- | --- |
| `db.sqlite3` on disk | filesystem is wiped when an instance recycles | `DATABASE_URL` → managed Postgres |
| Django's static serving | no web server to hand assets to | WhiteNoise serves `/static` from the bundle |

Local development is unchanged: with `DATABASE_URL` unset, settings falls back
to `db.sqlite3`, so `manage.py` and the full test suite still work untouched.

## Files added

- `vercel.json` — build command, routing, Python version
- `api/index.py` — WSGI entrypoint the Vercel runtime imports
- `.python-version` — pins CPython 3.12
- `.env.example` — every variable a deployed build needs

`requirements.txt` gained `psycopg[binary]`, `whitenoise` and `Brotli`.

## Steps

### 1. Provision Postgres on Neon

Vercel's Postgres integration is Neon under the hood, and the Neon dashboard
links directly from Vercel's Storage tab. Either route works.

1. Create a Neon project (region nearest to your Vercel function region).
2. Neon creates a `neondb` database and default branch automatically.
3. Copy the **pooled** connection string from the dashboard:

```
postgresql://USER:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require
```

**Use the pooled string, not the direct one.** The pooled hostname contains
`-pooler` and routes through PgBouncer. Serverless cold starts produce bursts
of simultaneous connections, and a direct connection will hit Neon's
`max_connections` limit and start returning `too many connections`. The
pooler absorbs the burst.

`sslmode=require` is already handled — the parser passes it through to libpq.
A Neon password can contain `@`, `:` and `/`, which are URL-encoded in the
dashboard string; the parser decodes them, so paste the string verbatim.

#### Neon specifics worth knowing

- **Auto-suspend.** The free tier suspends idle compute after ~5 minutes. The
  first query afterwards takes noticeably longer while the instance resumes.
  Expect a slow first request rather than an error.
- **Branch previews.** Neon branches give you an isolated database. Point a
  Vercel preview deployment at a Neon preview branch to keep preview traffic
  off production leads; otherwise previews share production data.
- **Pools vs pooled.** Neon also offers "Pooled" (PgBouncer) and
  "Session"/`-pooler` connection modes. For this app take **Pooled**.
  `CONN_MAX_AGE` is set to 60s, which is safe with PgBouncer in transaction
  mode.
- **No `psycopg` serverless driver needed.** Neon's HTTP/WebSocket driver is
  for the Node runtime. Python over TCP with `psycopg[binary]` against the
  pooled string is the correct setup here.

### 2. Set environment variables

Add everything from `.env.example` under Project Settings → Environment
Variables, marking the secrets Sensitive. Three of them are load-bearing and
fail in confusing ways if missed:

- `DATABASE_URL` — without it the deployment silently runs on a per-instance
  sqlite file, so leads and content edits appear to save and then vanish.
- `ALLOWED_HOSTS` — without the Vercel hostname, every request is a 400.
- `CSRF_TRUSTED_ORIGINS` — without it, pages render fine and every form POST
  fails 403. This is the most misleading failure mode, because the site looks
  completely healthy.

### 3. First deploy

```
vercel --prod
```

The build runs `collectstatic`. Migrations are deliberately **not** automatic
(`RUN_MIGRATIONS_ON_BUILD` defaults off) so a branch preview cannot migrate the
production database. Bootstrap once, from a machine that can reach the DB:

```bash
DATABASE_URL='postgresql://...' python manage.py migrate
```

### 4. Seed content

The production database starts empty — it does not inherit local sqlite data.
Seed it explicitly:

```bash
DATABASE_URL='postgresql://...' python manage.py seed_services
DATABASE_URL='postgresql://...' python manage.py capture_content
DATABASE_URL='postgresql://...' python manage.py createsuperuser
```

`capture_content` is safe to re-run and is how the 19 mirrored pages populate
production. Note it reconciles changed chunks, so re-running it after live
edits will overwrite them; use `--reset` only to restore the captured baseline.

### 5. Create an admin account

Local credentials do not carry over. The `admin` user exists only in the local
sqlite file.

## Operating notes

**Static payload is the main risk.** `main/static` is ~56MB, dominated by a
single 14MB `bathroom-hero-bg.jpg` used as a `center/cover` background on
`/bathroom-remodeling/`. WhiteNoise's Brotli precompression does not help,
because JPEG is already compressed. This inflates the function bundle and
slows that one page. Re-encoding that background at a sane resolution and
quality would cut it to a few hundred KB with no visible change.

**Migrations.** `RUN_MIGRATIONS_ON_BUILD=true` applies them on every
production build, which is convenient but means each deploy needs DB write
access. Leaving it off and migrating explicitly gives you a place to see
failures.

**Email.** The console backend is for local use only. In production a real SMTP
backend is required, and note that `crm.services` catches send failures and
logs them rather than surfacing them to the visitor — a broken SMTP config
will not show up as an error on the lead form, it will show up as missing
notifications. Check the function logs.

**Timeouts.** `maxDuration` is 30s. Lead capture writes two rows and sends up
to two emails, so there is a wide margin, but a cold start plus a slow SMTP
connection is the realistic worst case.

**Preview deployments** share the same database unless a separate
`DATABASE_URL` is scoped to them, so a preview build can write to production
leads. Scope a separate database if that matters.
