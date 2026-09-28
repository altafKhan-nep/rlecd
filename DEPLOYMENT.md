# Deploying to Render + Neon

Render runs the app in a container. Neon holds the Postgres database. Nothing
else is required, and no domain name has to be bought to start.

## How the pieces fit

| Concern | Local | Production |
| --- | --- | --- |
| Database | `backend/db.sqlite3` | Neon, via `DATABASE_URL` |
| Static files | Django's staticfiles app | WhiteNoise, pre-compressed by Brotli |
| Web server | `runserver` | Gunicorn, 2 workers |
| Hostname | `localhost` | `*.onrender.com`, auto-allowed |

With `DATABASE_URL` unset, `settings.py` falls back to SQLite, so
`backend/manage.py` and the full test suite still work with no configuration.

## Files that matter for deploys

- `render.yaml` — blueprint: build command, start command, env vars, health check
- `.python-version` — pins CPython 3.12, and must match `PYTHON_VERSION` in the
  service environment
- `scripts/check_python_version.py` — runs first in the build and fails loudly
  if the interpreter disagrees with the pin
- `scripts/preflight.py` — checks the database driver, env vars and security
  settings before a deploy
- `.env.example` — every variable a deployed build can use

## Steps

### 1. Provision Postgres on Neon

Create a Neon project and copy the pooled connection string. Use the **pooled**
host, not the direct one: Render opens more connections than a single Postgres
accepts, and the pooler is what keeps that from exhausting the limit.

The string looks like:

```
postgresql://USER:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require
```

### 2. Create the Render service

Two routes, both end up at the same configuration.

**Blueprint (recommended).** Render reads `render.yaml` from the repository root
and creates the service with every variable already set, including
`PYTHON_VERSION`. In the dashboard choose *New → Blueprint* and point it at the
repo. Only `DATABASE_URL` needs a value from you; `SECRET_KEY` is generated.

**Manual.** Create a Web Service and paste these:

- Build command:
  ```
  python scripts/check_python_version.py && pip install -r requirements.txt && python backend/manage.py collectstatic --noinput
  ```
- Start command:
  ```
  python backend/manage.py migrate --noinput && gunicorn home_improvement.wsgi:application --chdir backend --workers 2 --timeout 120
  ```
- Environment: `PYTHON_VERSION=3.12.14`, `DEBUG=False`, and the `DATABASE_URL`
  from step 1.

Migrations run in the start command, not the build. That is deliberate: a
database problem should fail the deploy with a readable log, not surface as a
traceback during a build that never needed the database.

### 3. Seed the content

On first deploy the site falls back to the captured static templates, so it is
never blank. To move the editable content into the database:

```bash
DATABASE_URL='postgresql://...' python backend/manage.py capture_content
```

This imports 19 pages, 58 sections and 15 services. It is idempotent — re-running
it refreshes imported rows without touching anything edited in the admin. **Do
not pass `--reset`**, which deletes existing rows first.

### 4. Create the first admin user

```bash
DATABASE_URL='postgresql://...' python backend/manage.py createsuperuser
```

Sign in at `/admin/`. See the CRM/CMS dashboard there.

## Verifying the database is really attached

The failure this guards against is silent: without `DATABASE_URL`, a deploy
still boots, still serves all 19 pages (from the template fallback), and the
admin login works — against a SQLite file that is destroyed on the next
recycle. Content edits then vanish.

Check which database a request is actually using:

```bash
curl -s https://YOUR-SERVICE.onrender.com/admin/login/ > /dev/null
```

Then confirm the environment variable is set on the service and that
`settings.py` parsed it. `scripts/preflight.py` reports the engine and host it
resolved, and fails if it lands on SQLite in a non-debug environment:

```bash
DATABASE_URL='postgresql://...' RENDER=1 DEBUG=False python scripts/preflight.py
```

## Two things the free tier will not tell you

**The service sleeps.** Free Render instances idle out after roughly 15 minutes
and take 30-60 seconds to wake on the first request. Fine for a site that gets
occasional visitors, noticeable for one you are editing all day.

**Uploads are lost on deploy.** The free tier has no persistent disk, so
anything written to `MEDIA_ROOT` — which includes the admin's image uploads —
disappears at the next deploy. The media add screen warns about this. A
persistent disk on a paid instance fixes it; Neon object storage is the
alternative if you want to stay on the free tier.

## Custom domain

Optional, and skippable. Attach one under *Settings → Custom Domains*. The
generated `*.onrender.com` hostname keeps working throughout, so this can wait
until there is a domain worth branding. Once attached, set `ALLOWED_HOSTS` to
the new hostname. `SITE_URL` and `CSRF_TRUSTED_ORIGINS` stay unset until the
site posts cross-origin, which it does not.
