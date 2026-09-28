# Integrity Home Improvements & Renovations

A premium, custom-built web platform for a leading Atlanta-based home remodeling company. This project focuses on high-end UI/UX design, search engine dominance, and seamless lead generation.

## 🚀 Project Overview
This application serves as the digital storefront for **Integrity Home Improvements**, highlighting over 21 years of craftsmanship in kitchen, bathroom, and basement remodeling.

### Key Features
*   **Modern UI/UX:** Built with a "Vibrant & Professional" aesthetic using Glassmorphism, Masonry Grids, and custom Hero animations.
*   **SEO Engineered:** Structured with geographic schema and optimized service area pages to rank #1 for Atlanta remodeling keywords.
*   **Interactive Service Map:** A custom-styled Google Maps integration showing service coverage across 20+ North Georgia cities.
*   **Conversion Focused:** Includes dynamic quote request forms, sticky navigation, and high-visibility CTAs.
*   **Database-Backed Content:** Every page is rendered from structured, editable `Page`/`Section` rows via the admin, not from hardcoded templates.

## 🛠️ Tech Stack
*   **Backend:** Python 3.12 / Django
*   **Frontend:** Server-rendered Django templates / Bootstrap 5 / JavaScript (Vanilla + GSAP for animations)
*   **Database:** SQLite (Development) / PostgreSQL via Neon (Production)
*   **Deployment:** Render (Gunicorn + WhiteNoise)

## 📂 Repository Structure

Backend and frontend are kept in separate top-level directories.

```
backend/                      # all Python
  manage.py
  home_improvement/           # project settings, urls, wsgi
  main/                       # public views and routes
  crm/                        # leads, contacts, services, admin pipeline
  content/                    # Page/Section models, import, render
  db.sqlite3                  # local dev database (gitignored)
  staticfiles/                # collectstatic output (gitignored)

frontend/                     # everything a visitor sees
  templates/                  # project-level overrides, e.g. admin/login.html
  templates_main/             # runtime shell: base.html + nav/foot/post partials
  source_templates/main/      # captured page sources used by `capture_content`
  static/                     # css, js, img
```

Two conventions are worth knowing before editing paths:

* **App labels stay `main`, `crm`, `content`.** `backend/` is prepended to
  `sys.path` by `manage.py` and `api/index.py`, so the apps import as top-level
  modules. Re-rooting them to `backend.main` would break the labels already
  recorded in migrations and the `{% extends 'main/x.html' %}` references in the
  stored page markup.
* **`frontend/templates_main` is on the template `DIRS` list, not inside an
  app.** The shell is referenced unprefixed (`base.html`,
  `partials/_nav_1.html`), so it only resolves because of that entry in
  `TEMPLATES['DIRS']`.

## ▶️ Common Commands

Run from the repository root:

```bash
.venv/bin/python backend/manage.py test
.venv/bin/python backend/manage.py runserver
.venv/bin/python backend/manage.py capture_content --dry-run
.venv/bin/python backend/manage.py collectstatic --noinput
.venv/bin/python scripts/preflight.py
```

`capture_content` is destructive to existing content unless you omit
`--reset`; it re-imports the captured templates over the live pages. Never run
it against a database whose content was edited in the admin.

---
Developed by **Quantum Core Software** 
*Focused on Quality, Guided by Integrity.*
# rlecd
