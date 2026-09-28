# CRM & Content Management Platform — Engineering Plan

**Client:** Integrity Home Improvements (Django site: `home_improvement`)
**Prepared by:** Quantum Core Software
**Date:** 2026-09-28
**Status:** Proposed — for review & approval

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current-State Audit](#2-current-state-audit)
3. [Target Architecture](#3-target-architecture)
4. [Technology Stack](#4-technology-stack)
5. [Frontend Integration & Dynamic Content Strategy](#5-frontend-integration--dynamic-content-strategy)
6. [CRM Modules & Feature Set](#6-crm-modules--feature-set)
7. [Data Model](#7-data-model)
8. [Security & Role Management](#8-security--role-management)
9. [Design Consistency System](#9-design-consistency-system)
10. [Scalability & Maintainability](#10-scalability--maintainability)
11. [Deployment & Maintenance](#11-deployment--maintenance)
12. [Delivery Roadmap](#12-delivery-roadmap)
13. [Effort Estimation & Resourcing](#13-effort-estimation--resourcing)
14. [Risks & Assumptions](#14-risks--assumptions)
15. [Appendix A — Current Content Inventory](#appendix-a--current-content-inventory)

---

## 1. Executive Summary

### The Problem

The existing site is a **static-content Django application**. Every piece of marketing content — headlines, service descriptions, project galleries, service areas, testimonials, phone numbers, brand identity — is hardcoded directly into 19 HTML template files. The database contains **only the default Django auth tables**; there is not a single domain model in the entire project.

The practical consequences:

- **Every content change requires a developer.** Editing a phone number means editing 14 files and deploying. A non-technical marketing manager cannot change a headline, add a project photo, or publish a new service.
- **Every lead is lost.** The contact forms send an email and immediately discard the data (`main/views.py:46-70`, `main/views.py:126-143`). If SMTP fails, the inquiry vanishes permanently. There is no lead database, no pipeline, no follow-up, no owner, no status.
- **The site is not currently deployable.** Production logs show `ModuleNotFoundError: No module named 'decouple'` — `python-decouple` is imported in `home_improvement/settings.py:15` but is absent from `requirements.txt`. The live app fails to boot.
- **The SEO surface is broken.** 103 unique image paths are referenced; 4 exist. 14 of 15 service pages have no `{% block title %}`, so they all inherit a single default title containing the word "Maryland" while the business markets itself as Atlanta. There is no sitemap, no robots.txt, no favicon, no 404 page, and no FAQ/Service/Review structured data.

### The Recommendation

Extend the existing Django project into a **single modular monolith** with a first-class content layer and a purpose-built lead-management CRM, exposed through a **branded admin interface** (Django admin + `django-unfold`) that reuses the website's own design tokens. Do **not** build a separate microservice, a headless CMS on a second platform, or a JavaScript SPA. The current stack is correct for the business; what is missing is the data layer and an operations UI.

The guiding principle: **make the templates generic, make the data specific.** The current HTML is the content. We invert that so the HTML is the layout and the database is the content — then the CRM simply becomes the interface for writing to that database.

### Outcomes

| Objective | Current State | Target State |
| --- | --- | --- |
| Content changes by marketing staff | Not possible (dev required) | Single form, live on publish |
| Lead capture reliability | Email-only, lossy | Durable DB record + email + audit trail |
| New service page launch | New template + view + URL + deploy | Data entry only |
| Brand consistency | 3 conflicting identities across templates | Single source of truth (`SiteSettings`) |
| Asset management | `{% static %}` paths to missing files | Managed media library with validation |
| SEO | Broken titles, no sitemap, no schema | Per-entity meta + JSON-LD + auto sitemap |
| Admin UI | Unstyled Django default | Branded, role-gated CRM |

---

## 2. Current-State Audit

### 2.1 Architecture as Built

```
home_improvement/           Project config (settings, urls, wsgi, asgi)
main/                       The only app — views, urls, templates, static
  models.py                 EMPTY (3 lines, comment only)
  admin.py                  EMPTY
  tests.py                  EMPTY (stub)
  migrations/               No migrations — no models exist
  templates/base.html       Global shell: nav, footer, SEO, JSON-LD
  templates/main/*.html     19 page templates, ~4,713 lines
  static/css/style.css      152 lines — design tokens + ~15 component classes
  static/js/script.js       67 lines — hero carousel only
  static/img/               4 files
db.sqlite3                  131 KB — auth tables only
passenger_wsgi.py           Hardcoded cPanel absolute paths
```

**View layer:** 21 function-based views. 15 are one-line `render()` pass-throughs. 2 handle `POST` (index, contact) with duplicated, copy-pasted validation and email logic. No class-based views, no mixins, no `LoginRequiredMixin`, no queryset optimization, no pagination.

**URL layer:** `from .views import *` (`main/urls.py:2`) — the entire views module is dumped into the URL namespace. No app namespace, no namespacing, no DRF router, no versioning. The 15 service URLs are hardcoded and static, so a new service requires a code change.

**Template layer:** No template inheritance beyond `base.html` (which itself has no parent, no partials, and no include tags). All 19 pages are self-contained monoliths. There are **no reusable partials** — so the nav, footer, and form markup are duplicated rather than composed.

**Data layer:** Zero models. Zero admin registrations. Zero tests. Zero forms module.

### 2.2 Findings — Severity Classified

#### P0 — Blocking / Production Breaking

| # | Finding | Location | Impact |
| --- | --- | --- | --- |
| P0-1 | `python-decouple` imported but not in `requirements.txt` | `settings.py:15`, `requirements.txt` | **App fails to start on deploy.** Confirmed in `stderr.log`. |
| P0-2 | Leads never persisted — `send_mail` then discard | `main/views.py:44-70`, `main/views.py:126-143` | Every inquiry lost if SMTP fails. No pipeline, no follow-up. |
| P0-3 | `print()` for error handling; bare `except Exception` | `main/views.py:68`, `main/views.py:148-149` | No structured logging, no alerting, no traceback retention. |
| P0-4 | 99 of 103 referenced images do not exist | `static/img/` | Broken layout on every page; hero backgrounds and galleries render empty. |
| P0-5 | `contact.css` and `area_we_serve.css` referenced but missing | `base.html:77-78` | 404s on every page load. |

#### P1 — Content Integrity & Brand

The site carries **three mutually exclusive brand identities** and **two geographies** simultaneously. This is a template that was forked from a Maryland client and re-skinned for Atlanta without completing the rewrite.

| Signal | Value A | Value B | Occurrences |
| --- | --- | --- | --- |
| Company name | "Integrity Home Improvements" | "REAL LIFE EXPERIENCE LLC" | 12 vs 14 |
| Company name (typo) | — | "IREAL LIFE EXPERIENCE" | 1 (`index.html:451`) |
| Geography | Maryland / Baltimore / Reisterstown | Atlanta / Buckhead / GA | 15 vs 17 |
| Phone | `(443) 898-3143` (Maryland) | `+1 770 949-3500` (Georgia) | 14 vs 1 |
| Years in business | 18 | 20+ / 21+ | multiple |
| Email domain | `agm@rlecd.com` | `ihiatlanta.com` | 3 vs 4 |
| Canonical domain | `ihiatlanta.com` (`base.html:13`) | `homeimp.quantumcoresoftware.com` (`ALLOWED_HOSTS`) | conflicting |

Additional P1 issues:

- **`additions.html` serves the wrong content.** The route is `/home-additions/`, the nav label is "Home Additions", but the hero class is `hero-condo`, the `<h1>` is **"Condo Transformations"**, and it loads `condo-hero.jpg`, `condo1.jpg`, `condo2.jpg`. The page is a condo page under an additions URL — a direct SEO and trust problem.
- **Two lead-capture forms with incompatible `service` value contracts.** `index.html:522-537` posts display strings (`"Kitchen Remodeling"`); `contact.html:93-98` posts slugs (`"kitchen"`). Each view re-maps independently (`views.py:90-96`). `contact.html` exposes only **4 of 15** services; the other 11 are unreachable from that form. No shared source of truth.
- **`home_improvement.html` duplicates navigation** — it re-lists Kitchens/Bathrooms/Basements/Painting already covered by dedicated pages.
- **Mobile menu is broken for 11 of 15 services.** `base.html:147-157` uses `href="#"` for Painting, Home Improvement, Patios & Decks, Cabinets, Woodworking, Hardscaping, Walkway Designs, Pergolas, Lead Removal, Shed Builder, Lead Renovator. Desktop nav (`base.html:100-114`) links them correctly. Mobile users cannot reach most of the service catalogue.
- **Canonical/Open Graph inconsistencies.** `base.html:13` canonicalises to `ihiatlanta.com`; `base.html:18` hardcodes `og:url` to `https://ihiatlanta.com/` for *every* page; `og:image` (`base.html:21`) and JSON-LD `image` (`base.html:35`) point to files that don't exist.

#### P2 — SEO, Quality, Engineering Debt

- **14 of 15 service pages have no `{% block title %}`** — they all render `#1 Maryland Home Renovations & Remodeling`. Near-total title-tag failure across the highest-value pages.
- No `sitemap.xml`, no `robots.txt`, no favicon, no `404.html`/`500.html`.
- Structured data is a single hardcoded `HomeAndConstructionBusiness` blob in `base.html:29-71`. **No `Service`, `FAQPage`, `BreadcrumbList`, or `Review` schema** — the highest-leverage SEO opportunities are unexploited.
- No testimonials, reviews, or ratings anywhere on the site.
- No privacy policy or terms page — a GDPR/CCPA exposure given form data is collected.
- `SECURE_BROWSER_XSS_FILTER = True` (`settings.py:71`) was **removed in Django 4.0**; dead configuration.
- `TIME_ZONE = 'UTC'` (`settings.py:141`) — a US-East business scheduling against UTC. Will misfire all appointment logic.
- `from .views import *` (`urls.py:2`) — no namespace isolation; a future API or second app will collide.
- `static(settings.STATIC_URL, ...)` applied unconditionally in `urls.py:25` (should be debug-only).
- `django-extensions` in `requirements.txt` but not in `INSTALLED_APPS` — unused dependency.
- `settings.py` docstring claims Django 6.0.4; `requirements.txt` pins 5.2.13; `passenger_wsgi.py` targets Python 3.10.
- `tests.py` is a stub. Zero test coverage on the only logic that exists (form handling).
- Extensive **inline `<style>` blocks** duplicated per service page (e.g. `kitchen.html:5-126`) — ~120 lines of page-specific CSS that should be component classes driven by design tokens.

### 2.3 Content Inventory (what the CRM must absorb)

| Asset | Count | Notes |
| --- | --- | --- |
| Service pages | 15 | Kitchen, Bathroom, Basement, Additions, Painting, Home Improvement, Patios & Decks, Cabinets, Woodworking, Hardscaping, Walkway Designs, Pergolas, Lead Removal, Shed Builder, Lead Renovator |
| Project gallery images | ~100 | 20 (bathroom), 6 (kitchen), 5 (basement), 6 (pergola), 5 (hardscape), 4 (shed), 4 (cabinet), 4 (wood), 4 (walkway), 3 (paint), 3 (condo), 3 (deck), 1–2 others |
| Service areas | 20 homepage + 20 on Areas page | Two overlapping, inconsistent lists; no coordinates, no grouping model |
| Feature/value cards | ~11 homepage | Why Choose Us (4) + Values grid (2) + mini-features (3) + tiles (5) |
| Process steps | 4 | Lead removal remediation (Containment → Removal → HEPA → Clearance) |
| Trust stats | 13 | Years, projects completed, licensed, code compliance |
| Contact channels | 3 | Phone, email, location — duplicated across 19 templates |
| Certifications/badges | 3 | BBB accreditation CTA, EPA Lead Renovator (RRP), Financing (Nelnet) |
| Partner integrations | 2 | Nelnet financing URL, BBB URL |
| Forms | 2 | Homepage + contact, both unvalidated, no spam protection |

---

## 3. Target Architecture

### 3.1 Architectural Style: Modular Monolith

A modular monolith is the correct choice here. The business has one team, one deployment target (cPanel/Passenger), and traffic in the low-thousands of pageviews/month. Microservices would add operational cost with no benefit. The architecture is a **layered monolith with strict app boundaries** enforced by convention and import rules.

```
┌──────────────────────────────────────────────────────────────────────┐
│                         PRESENTATION LAYER                          │
│                                                                      │
│  Public Site (Django Templates)          CRM Admin (django-unfold)   │
│  ├ base.html → dynamic shell             ├ Dashboard (KPIs)          │
│  ├ service_detail.html (generic)         ├ Content Studio            │
│  ├ page_detail.html   (generic)          ├ Lead Pipeline (kanban)    │
│  ├ area_detail.html                     ├ Media Library             │
│  └ partials/ (nav, footer, seo, forms)   ├ SEO Manager               │
│                                           └ Site Settings            │
└────────────────────────┬──────────────────────────┬──────────────────┘
                         │                          │
              ┌──────────┴──────────────────────────┴──────────┐
              │              SERVICE / API LAYER              │
              │  django-tag helpers · selectors · presenters  │
              │  DRF serializers · throttling · permissions   │
              └──────────────────────────┬────────────────────┘
                                         │
              ┌──────────────────────────┴────────────────────┐
              │                 DOMAIN LAYER                   │
              │                                                 │
              │  content   │  crm     │  users   │  core       │
              │  (Service, │  (Lead,  │  (User,  │  (SiteSet-  │
              │   Page,    │   Contact,│   Role,  │   tings,    │
              │   Project, │   Task,  │   Audit) │   Media)    │
              │   Area,    │   Appt,  │          │             │
              │   FAQ,     │   Est.)  │          │             │
              │   Testim.) │          │          │             │
              └──────────────────────────┬────────────────────┘
                                         │
              ┌──────────────────────────┴────────────────────┐
              │            INFRASTRUCTURE LAYER               │
              │  PostgreSQL 16 · Redis · Celery · S3/R2 media │
              │  WhiteNoise · django-storages · django-environ│
              └───────────────────────────────────────────────┘
```

### 3.2 Django App Layout

Reorganize `main` into purpose-built apps. This is the foundation for everything else.

```
home_improvement/           # project config, split settings
core/                       # SiteSettings, MediaAsset, Navigation, Redirect, AuditLog
content/                    # Service, Page, Project, ServiceArea, FAQ, Testimonial,
                            #   Feature, ProcessStep, Block/Section, EmailTemplate
crm/                        # Lead, Contact, Task, Appointment, Estimate, Activity
users/                      # Custom User, Role, Profile, permissions
api/                        # DRF v1 serializers, viewsets, routers, throttles
frontend_views/             # Public site views (replaces main/views.py)
management/                 # Custom admin actions, import/export, seeding
```

**Migration strategy:** `main` is retained as a thin shim during Phase 2 so existing URLs never 404 while the new generic templates come online. It is deleted in Phase 3.

### 3.3 Content Rendering Strategy: Section Blocks

This is the central architectural decision and deserves justification.

The 15 service pages share a **consistent anatomy** despite divergent markup:

1. Hero (full-bleed image, eyebrow, H1, subtitle) — unique class/asset per page
2. Intro split (image + prose + bulleted feature list)
3. Feature/benefit cards (3–5, icon + title + body)
4. Project gallery (masonry or grid, 1–20 items, each with title + caption)
5. Process steps (some pages: 4 steps, e.g. lead removal)
6. Trust/certification bar (some pages)
7. Stats bar (some pages)
8. CTA banner (all pages)

Rather than 15 bespoke templates, we model this as **an ordered list of typed sections** rendered by a single generic template. Each service is a row in `Service`; each section is a `ServiceSection` with a `section_type` discriminator and JSON payload validated per type.

```
Service (1) ──── (N) ServiceSection
                    section_type ∈ {hero, intro_split, features, gallery,
                                    process_steps, stats, trust_bar, cta,
                                    faq, rich_text, image_gallery, two_col}
```

**Why this beats the alternatives:**

| Approach | Verdict |
| --- | --- |
| 15 bespoke templates (status quo) | Rejected — every content change is a code change; impossible to add a service without a deploy |
| Wagtail | Strong option, but introduces a second mental model, its own routing, and a migration that touches every URL. Overkill for 15 static service pages. Reconsider if the client needs multi-page publishing workflows or a news/blog engine. |
| Headless CMS + JS frontend | Rejected — destroys server-rendered SEO, adds a Node toolchain and origin layer to a cPanel host, and buys nothing at this traffic level |
| **Section blocks (chosen)** | One generic template, data-driven sections, designers can reorder/replace sections without a deploy, and a marketing-friendly "add section" UI |

**The escape hatch:** every service also carries a `custom_template` override field. If a page genuinely needs bespoke markup, an editor can point it at a named template — the generic renderer is the default, not a straitjacket.

### 3.4 Data Flow

```
  EDITOR (CRM UI)
        │
        ▼
  Django Form ──► ModelForm validation ──► pre_save hook: slug generation
        │                                        slug uniqueness check
        │                                        SEO completeness check
        ▼
  Django Model ──► post_save: bump ContentVersion, enqueue revalidation
        │
        ├──► Celery task: render JSON-LD, regenerate sitemap segment
        ├──► Redis: bust cache keys for affected pages
        └──► AuditLog entry (who / what / when / diff)
        │
        ▼
  PUBLIC SITE
   request ──► page cache lookup (Redis)
                 │ miss
                 ▼
               ORM query (select_related / prefetch)
                 │
                 ▼
               Generic template renders sections
                 │
                 ├──► HTML response
                 ├──► cached
                 └──► SEO context (meta, canonical, OG, JSON-LD, breadcrumbs)

  LEAD SUBMISSION
   POST ──► throttling ──► hCaptcha ──► Django Form validation
        │
        ├──► Lead row created (status=NEW, source/UTM captured)
        ├──► AuditLog + LeadActivity entries
        ├──► Celery task: admin notification email (retry x3, dead-letter)
        ├──► Celery task: customer auto-responder from EmailTemplate
        └──► redirect w/ confirmation
```

---

## 4. Technology Stack

### 4.1 Core Stack

| Layer | Choice | Rationale |
| --- | --- | --- |
| Language | **Python 3.12** (migrate from 3.10) | Current LTS; 3.10 EOL Oct 2026. Upgrade before CRM work lands. |
| Framework | **Django 5.2 LTS** | Already in production. LTS support to Apr 2028 — correct long-term choice. Do **not** adopt the 6.0 line for a client system; shorter support window. |
| Database | **PostgreSQL 16** | Required. SQLite has no concurrent writes, no `JSONB`, no full-text search, no `SELECT … FOR UPDATE` — all needed for lead assignment and section payloads. |
| Cache / broker | **Redis 7** | Page cache, session store, Celery broker, rate-limit counters. |
| Task queue | **Celery 5.4** + beat | Async email with retry, sitemap regeneration, image derivative generation, lead scoring, webhooks. |
| Media storage | **S3 or Cloudflare R2** + `django-storages` | cPanel disk is not a CDN. Removes `MEDIA_ROOT` from the deploy path entirely. |
| Static files | **WhiteNoise** | Serves `collectstatic` output through Passenger with far-future cache headers. |
| API | **Django REST Framework 3.15** | Backs the CRM SPA-lite, mobile, and any future headless consumer. |
| Config | **django-environ** | Replaces the currently-broken `decouple`. Validates types, fails fast on missing vars. |

### 4.2 CRM Admin Interface

This is the pivotal choice, so it is stated explicitly.

| Component | Choice | Rationale |
| --- | --- | --- |
| Admin framework | **Django admin + [django-unfold](https://github.com/unfoldadmin/django-unfold)** | Delivers a **fully branded, responsive, modern admin** as a drop-in theme. Supports custom brand colors, a custom logo, and a custom CSS layer — so the CRM visually matches the website with ~50 lines of config instead of a bespoke React app. Built on Tailwind, actively maintained, zero lock-in (it *is* Django admin). |
| Rich content | **django-ckeditor** (or TinyMCE) | WYSIWYG for prose blocks. Avoids hand-writing HTML. |
| Admin UX layer | Custom `ModelAdmin` classes, `TabularInline`, custom actions, `SimpleListFilter`, `annotate` for pipeline views | Where the real CRM ergonomics live: bulk reassign, bulk status change, filters for "unassigned / stale / high-value". |
| Charts | **django-chartjs** or Chart.js via custom admin template | Dashboard KPIs. |

**Why not build a separate React CRM?** A separate SPA requires a Node build pipeline, a second deployment target, a CORS surface, and a second auth system — roughly 3–4× the build cost and a permanent maintenance burden — to deliver forms and list views that `django-unfold` provides out of the box, styled to match the brand. Revisit only if the client later needs a genuine customer-facing portal with accounts, quotes, and payments; at that point build a public-facing React app and reuse the same DRF API. **The API-first design in this plan means that migration remains open.**

### 4.3 Supporting Libraries

| Purpose | Library | Notes |
| --- | --- | --- |
| SEO metadata | `django-override-settings` + custom `SeoMixin` | Title, description, canonical, OG, Twitter cards per entity |
| JSON-LD | Hand-built serializer + `structlog` logging | Schema.org graph generation, validated in CI |
| Sitemap | `django-sitemap` | Auto-generated from live, published records |
| Forms validation | `django-crispy-forms` + `django-recaptcha`/`hcaptcha` | Consistent admin form rendering + spam defence |
| Object-level permissions | **django-guardian** | Per-object lead ownership and assignment |
| Soft delete | `django-parler` (translations) / custom `TimeStampedSoftDeleteModel` | Never hard-delete content with live inbound links |
| Background jobs | `django-huey` *(alternative)* | Lighter than Celery if ops capacity is limited |
| PDF quotes | `weasyprint` or `xhtml2pdf` | Generate branded estimate PDFs from templates |
| Audit trail | `django-simple-history` | Every model change recorded with actor + diff |
| Testing | `pytest-django`, `factory_boy`, `pytest-cov` | 80% coverage target on domain layer |
| Linting/format | `ruff` + `black` | Replaces the current unlinted codebase |

### 4.4 Deliberate Non-Adoptions

| Rejected | Why |
| --- | --- |
| Headless CMS (Contentful, Sanity, Strapi) | Second platform, second bill, second failure mode, SEO/CORS cost. No benefit at 15 pages. |
| Microservices | No team to operate them; cPanel is a single host. |
| GraphQL | REST + DRF covers the use case with far less surface area. |
| MongoDB | Relational data with heavy joins (Service → Sections → Media → SEO). PostgreSQL is correct. |
| Kubernetes / Docker | cPanel + Passenger is the deployment reality. |
| WordPress plugin ecosystem | Already in Django; migrating would lose the server-rendered control and add a larger attack surface. |

---

## 5. Frontend Integration & Dynamic Content Strategy

### 5.1 Integration Principles

1. **Zero-downtime URL preservation.** All 19 existing URLs stay byte-identical forever. The CRM must never be able to break a live link.
2. **Progressive enhancement.** Every page renders fully without JavaScript. The existing `script.js` carousel is 67 lines of vanilla JS and stays that way — no framework is introduced into the public site.
3. **Template-side isolation.** Public templates receive a flat, read-only context assembled by presenters. They never call the ORM directly. This keeps the data layer swappable.
4. **Zero DB access on the critical path where cacheable.** Published content is read from Redis; the database is the system of record, not the request path.

### 5.2 The Context Processor

A single `site_context` processor exposes the global shell data to every template, eliminating the 19-file duplication of phone numbers, social links, and navigation.

```python
# core/context_processors.py  (conceptual)
{
    "site": {                          # from SiteSettings singleton
        "brand_name": "...",
        "phone_display": "(443) 898-3143",
        "phone_e164": "+14438983143",
        "email": "...",
        "address": {...},
        "hours": [...],
        "social": {...},
        "logo": <MediaAsset>,
    },
    "nav_items": [...],                # from Navigation/MenuItem, tree-aware
    "footer_columns": [...],           # from Navigation
    "services": [...],                 # published Services, for mega-menu
    "service_areas": [...],            # grouped ServiceAreas
    "legal_links": [...],              # privacy/terms
    "analytics": {...},                # GA4 / GTM ids from SiteSettings
}
```

`base.html` is then rewritten once. The nav's 15 hardcoded `<li>` pairs and the footer's 4 hardcoded columns collapse into two loops, and the mobile menu's 11 `href="#"` bugs disappear because both menus render from the same `MenuItem` queryset.

### 5.3 Template Tags & Helpers

```python
{% load content_tags %}

{% site_logo class="footer-logo" %}                 → asset URL + srcset + width/height
{% site_phone as phone %}                           → display string, tel: href
{% seo_head page=page %}                            → title, description, canonical, OG, Twitter
{% jsonld page=page %}                              → schema.org graph
{% breadcrumbs page=page %}                         → BreadcrumbList + visible trail
{% section_renderer page=page %}                    → renders ordered Section rows
{% media asset=project.image sizes="(max-width:768px) 100vw, 50vw" %}
{% cta_url service=service %}                       → campaign-tracked contact URL
```

`{% section_renderer %}` is the only genuinely new template construct. It is a single `{% include %}` driven by a `section_type → template` registry, so any section type can be added without touching the renderer.

### 5.4 Section Registry

```python
SECTION_REGISTRY = {
    "hero":            "content/sections/hero.html",
    "intro_split":     "content/sections/intro_split.html",
    "features":        "content/sections/features.html",
    "gallery":         "content/sections/gallery.html",
    "process_steps":   "content/sections/process_steps.html",
    "stats":           "content/sections/stats.html",
    "trust_bar":       "content/sections/trust_bar.html",
    "cta":             "content/sections/cta.html",
    "faq":             "content/sections/faq.html",
    "rich_text":       "content/sections/rich_text.html",
    "two_column":      "content/sections/two_column.html",
}
```

Each section template defines its own `{% block %}`s so a global restyle (e.g. `cta.html` button styling) updates all 15 pages at once — directly serving the design-consistency requirement.

### 5.5 SEO Rebuild

Per-entity fields (`seo_title`, `seo_description`, `seo_keywords`, `og_image`, `canonical_url`, `noindex`) live on `Service`, `Page`, and `ServiceArea`. A `SeoMixin` + `SeoAdmin` generates:

- `<title>`, meta description, canonical (`request.build_absolute_uri`), robots directives
- Full Open Graph + Twitter card set — replacing the single hardcoded `og:url` at `base.html:18`
- **JSON-LD graph per page type:**
  - `Organization` / `HomeAndConstructionBusiness` (from `SiteSettings`, replacing `base.html:29-71`)
  - `Service` + `Offer` on every service page
  - `FAQPage` on every page with an FAQ section
  - `BreadcrumbList` site-wide
  - `AggregateRating` where real reviews exist
- `sitemap.xml` covering services, pages, and **service-area pages** — the 40-city `Areas We Serve` list is the single highest-value untapped SEO asset on this site and is currently one static page
- `robots.txt` and per-section `noindex` for drafts/previews

### 5.6 Lead Capture Rebuild

The duplicated `views.py` logic is replaced by one shared path:

```python
# crm/services/lead_capture.py  (conceptual)
def capture_lead(request, form_class, source):
    form = form_class(request.POST)
    if not form.is_valid():
        return form_response(form)

    lead = Lead.objects.create(
        **form.cleaned_data,
        source=source,
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
        utm_source=form.cleaned_data.get("utm_source"),
        utm_medium=form.cleaned_data.get("utm_medium"),
        utm_campaign=form.cleaned_data.get("utm_campaign"),
        gclid=form.cleaned_data.get("gclid"),
        landing_page=request.get_full_path(),
        referrer=request.META.get("HTTP_REFERER", "")[:512],
    )
    ...
```

Because `service` becomes a `ForeignKey` to the `Service` model, the `index.html` vs `contact.html` option-value contract is **solved permanently** — both forms render from the same queryset, all 15 services appear on both, and adding a service updates every form instantly. Emails become Celery tasks with retry and dead-lettering, and are rendered from editable `EmailTemplate` rows rather than f-strings in a view.

---

## 6. CRM Modules & Feature Set

### Module 1 — Dashboard (Home)

The operational landing screen. Every widget is a live queryset, no external BI.

- **Lead KPIs:** new today / this week / this month; unassigned count (with a red badge if > 0); open pipeline value; conversion rate; average response time
- **Pipeline snapshot:** count by status as a horizontal bar
- **Urgent attention:** leads older than 24h with no owner, stale quotes, appointments unconfirmed in 48h
- **Content health:** services/pages missing a featured image, SEO title, or description; broken/missing media; unpublished drafts; pages with no leads in 90 days
- **Recent activity:** audit feed of who changed what
- **Quick actions:** new lead, new service, upload media

### Module 2 — Lead Management (Core CRM)

The primary business module — it closes the "every lead is lost" gap.

- **Inbox/kanban view** by status, with inline card preview
- **Filters:** status, stage, source, service, owner, city/area, date range, value band, has-appointment, is-hot
- **Lead detail:** full timeline — every status change, note, call log, email, form submission
- **Quick actions:** assign to me, call, email, SMS, schedule consultation, convert to opportunity
- **Bulk operations:** reassign, change status, add tag, export CSV
- **Deduplication:** normalised email/phone matching with a merge prompt on create
- **Lead scoring:** rule-based (service value + source + area in service range + budget signal + message keyword match) → 0–100 score, auto-flagged "hot"
- **Round-robin assignment** by service specialty and/or territory
- **Import:** CSV for migrating historical leads

### Module 3 — Contacts & Companies

- `Contact` (person) belongs to an optional `Company` (BGC, HOA, property manager, builder)
- Contact ↔ Lead ↔ Appointment ↔ Estimate relationships
- Full interaction history across all related leads
- Custom fields (property address, referral source, VIP flag)

### Module 4 — Pipeline & Estimates

- **Opportunity/Project:** a lead that has been qualified — budget band, target start date, property address
- **Estimate:** line items (description, qty, unit price, markup), tax, discount, total
- **Branded PDF generation** and email-to-client with an e-sign handoff
- **Status workflow:** Draft → Sent → Viewed → Approved/Declined → Converted
- Commission tracking per estimator (optional, phase-gated)

### Module 5 — Appointments & Calendar

- Consultation scheduling tied to a lead
- Availability per team member, buffer time, timezone-aware (`America/New_York` — fixing the current UTC bug)
- Day/week agenda view in the admin; email + SMS reminders
- Reschedule/cancel flow with reschedule links

### Module 6 — Tasks & Follow-ups

- Task: title, due date, priority, assigned to, related lead
- Overdue highlighting, "my tasks" filter, recurring task templates (e.g. "call back in 3 days")
- One-click create from any lead record

### Module 7 — Content Studio

The CMS surface for all website content.

- **Services:** list with status, ordering, completeness indicator, publish/unpublish, duplicate, preview
- **Pages:** generic marketing pages (About, Contact, Privacy, Terms) with section-based editing
- **Home page:** edit each homepage section (hero carousel slides, features grid, tile gallery, areas grid, stats, partner/financing blocks) via inlines
- **Section editor:** add / remove / reorder / duplicate sections per page; live preview
- **Services taxonomy:** categories (Remodeling, Outdoor, Specialty, Remediation) for nav grouping and cross-links
- **Publishing workflow:** Draft → In Review → Published, with optional scheduled publish and preview token

### Module 8 — Project Portfolio

- `Project` records with image, title, caption, location, service, year, tags, featured flag, sort order
- Bulk upload with drag-and-drop multi-file
- Auto-attach to service galleries via service FK
- Alt-text required at the form level (SEO + accessibility gate)

### Module 9 — Service Areas

- `ServiceAreaGroup` (Local Core, North & Carroll, East & Baltimore, South & Howard)
- `ServiceArea` with name, slug, group, state, county, **latitude/longitude**, active flag
- Radius-based logic to auto-suggest an area from a lead's ZIP
- Coordinate-driven map rendering (replaces two hand-maintained lists)
- Optional per-area landing pages for programmatic SEO

### Module 10 — Social Proof

- `Testimonial`: author name, company, rating (1–5), body, service, area, date, featured, published
- Feed to service pages and homepage; feeds `AggregateRating` JSON-LD
- Manual entry or import from Google/Yelp

### Module 11 — FAQ Management

- `FAQCategory` + `FAQ`: question, answer (rich text), related services, ordering
- Placement: global, per-service, or per-area page
- Auto-emits `FAQPage` schema

### Module 12 — Media Library

- Central asset store: image/video/document, with focal point, alt text, credit, tags
- Upload with progress, bulk operations, search, filter by usage
- **Validation at upload:** missing-asset detection against rendered pages, WebP/AVIF generation, responsive `srcset`, dimensions recorded to prevent CLS
- Storage on S3/R2 behind a CDN

### Module 13 — SEO Manager

- Per-entity SEO fields with a live SERP snippet preview
- Bulk audit: missing titles, duplicate titles, missing descriptions, missing OG images, broken canonicals
- Redirect manager (301) with hit counting — essential safety net for slug changes
- Keyword tracking per service/area page
- robots directives and `noindex` toggles

### Module 14 — Site Settings & Navigation

- `SiteSettings` singleton: brand name, legal name, logo, favicon, phone (display + E.164), email, address, geo-coordinates, business hours, social profiles, payment/financing links, analytics IDs, robots directives
- `Navigation` / `MenuItem`: header, mega-menu, mobile, footer columns — drag-to-reorder, per-item visibility
- **Kills the three-brand-identity problem:** the phone number exists in exactly one place.

### Module 15 — Email Templates

- Editable subject + HTML/text body with `{{ lead.name }}`-style variables
- Preflight preview renderer with sample data
- Send log with delivery status per recipient
- Transactional templates: lead notification, customer auto-responder, appointment confirmation, estimate delivery, review request
- Replaces every f-string email currently in `views.py`

### Module 16 — Users, Roles & Audit

- Custom `User` extending `AbstractUser` with phone, role, avatar, is_active_staff, last_login_ip
- `Role` → permission groups; per-user role assignment
- `AuditLog`: actor, action, object, object_id, field diffs, IP, user agent, timestamp — immutable
- "Who changed this and when?" is answerable for every record

### Module 17 — Reporting

- Leads by source / service / area / month; conversion funnel; response-time distribution; revenue by service and estimator
- Exportable CSV; simple chart widgets
- **Scheduled email digests** (daily lead summary, weekly pipeline report) via Celery beat

### Module 18 — Integrations (Phase-Gated)

- Google Analytics 4 + Google Search Console
- Google Maps API replacing the fragile hardcoded `pb=` embed iframe
- Nelnet financing link management (currently hardcoded `index.html:605`)
- BBB accreditation block toggle
- Optional: QuickBooks / Xero, Twilio SMS, Slack alerts for new high-value leads

---

## 7. Data Model

### 7.1 Entity-Relationship Overview

```
┌────────────────────┐
│      Role          │ 1
└────────┬───────────┘
         │ M:N via UserRole
┌────────▼───────────┐
│      User          │ (custom, AbstractUser)
│  ├── TeamMember 1:1 │
│  └── AuditLog (actor) M:N
└────────┬───────────┘
         │ owns
┌────────▼───────────┐         ┌──────────────────┐
│       Lead         │────────▶│     Service      │ M:1
│  ├── Contact  M:1   │         │  └── Category M:1│
│  ├── Company  M:1   │         └────────┬─────────┘
│  ├── Owner → User   │                  │ M:N
│  ├── LeadActivity N │         ┌────────▼─────────┐
│  ├── Task        M:N│         │   Project (M:N)  │
│  ├── Appointment M:N│         └────────┬─────────┘
│  └── Estimate    1:N│                  │ M:1
└────────┬───────────┘         ┌────────▼─────────┐
         │                     │   MediaAsset     │
         │ 1:1                 └────────┬─────────┘
┌────────▼───────────┐                  │ M:1
│    Opportunity     │         ┌────────▼─────────┐
└────────┬───────────┘         │  ContentVersion │
         │ 1:N                 └──────────────────┘
┌────────▼───────────┐
│     Estimate       │
└────────────────────┘

┌──────────────┐ 1   ┌──────────────┐ N   ┌──────────────────┐
│  ServiceArea │─────│  ServiceArea │     │  MediaAsset      │
│   Group      │     │              │     │  (FocalPoint,    │
└──────────────┘     └──────┬───────┘     │   variants, alt) │
                           │ 1:N          └──────────────────┘
                  ┌────────▼────────┐
                  │  ServiceAreaPage│  (optional programmatic SEO)
                  └─────────────────┘

┌──────────────┐ 1   ┌──────────────┐ N   ┌──────────────┐
│     Page     │─────│    Section   │     │  Testimonial │
└──────────────┘     └──────────────┘     └──────────────┘
```

### 7.2 Core Content Models

**`SiteSettings`** (singleton — `enforce_unique` on a constant key)
`key` (unique, default `"primary"`), `site_name`, `legal_name`, `tagline`, `logo` → `MediaAsset`, `favicon` → `MediaAsset`, `phone_display`, `phone_e164`, `email`, `address_line1`, `address_line2`, `city`, `state`, `postal_code`, `latitude`, `longitude`, `business_hours` (JSON), `social_links` (JSON), `analytics_ids` (JSON), `financing_url`, `bbb_url`, `google_maps_embed`, `default_seo_title`, `default_seo_description`, `default_og_image` → `MediaAsset`, `updated_at`

> Single source of truth for every value currently duplicated across 19 templates and 2 competing brand identities.

**`MediaAsset`**
`id`, `file` (S3/R2), `filename`, `kind` (`image`/`video`/`document`), `mime_type`, `width`, `height`, `filesize`, `alt_text` **(required)**, `title`, `caption`, `credit`, `focal_point_x`, `focal_point_y`, `tags` (M2M → `Tag`), `uploaded_by` → `User`, `is_public`, `created_at`
→ generates `ImageVariant` rows (thumbnail/card/hero/og) as WebP + AVIF + JPEG fallback with `srcset`.

**`ServiceCategory`**
`name`, `slug` (unique), `description`, `icon_class`, `ordering`, `is_active`, `show_in_nav`

**`Service`**
`category` → `ServiceCategory` (M:1, PROTECT), `name`, `slug` (unique), `short_name`, `tagline`, `excerpt`, `is_primary` (flag: Kitchen/Bathroom/Basement = "flagships"), `icon_class`, `hero_image` → `MediaAsset`, `card_image` → `MediaAsset`, `custom_template` (nullable escape hatch), `ordering`, `status` (draft/review/published/archived), `published_at`, `show_in_nav`, `is_featured`
+ **SEO mixin:** `seo_title`, `seo_description`, `seo_keywords`, `og_image` → `MediaAsset`, `canonical_url`, `noindex`
+ **optional JSON-LD overrides:** `schema_json` (JSONField)

**`Section`** — polymorphic content blocks
`page` → `Page` (nullable FK, CASCADE), `service` → `Service` (nullable FK, CASCADE), `block_key` (for named singleton blocks, e.g. `"home.hero"`, `"home.areas"`, `"global.footer"`, `"global.cta"`), `section_type` (choices, indexed), `content` (JSONField), `ordering` (PositiveInteger), `is_visible`, `visibility_rule` (e.g. `"mobile_only"` / `"desktop_only"`), `custom_css_classes`, `background_media` → `MediaAsset`

> `page` XOR `service` XOR `block_key` — exactly one owner. Enforced in `clean()`. This is the mechanism behind the generic-template strategy in §3.3.

**`Page`**
`title`, `slug` (unique), `page_type` (about/contact/privacy/terms/landing), `template` (default or custom), `is_published`, `published_at`, `exclude_from_nav`, `ordering` + **SEO mixin**

**`Project`** (portfolio)
`title`, `slug`, `service` → `Service` (M:1, nullable, SET_NULL), `image` → `MediaAsset` (PROTECT), `caption`, `location_text`, `area` → `ServiceArea` (nullable), `year`, `tags` (M2M), `is_featured`, `ordering`, `is_published`
→ `Project.services` (M2M through) for cross-listing a project under several services.

**`ServiceAreaGroup`** — `name`, `slug`, `description`, `icon_class`, `ordering`
**`ServiceArea`** — `name`, `slug`, `group` → `ServiceAreaGroup` (PROTECT), `state`, `county`, `latitude`, `longitude`, `is_active`, `ordering`, `intro_copy`
**`ServiceAreaPage`** — `area` → `ServiceArea` (1:1), `hero_image`, `body`, `local_references` (JSON), `seo_*`

**`Testimonial`** — `author_name`, `company`, `rating` (1–5, indexed), `body`, `service` → `Service` (nullable), `area` → `ServiceArea` (nullable), `source`, `is_published`, `is_featured`, `ordering`
**`FAQCategory`** — `name`, `slug`, `ordering`
**`FAQ`** — `category` → `FAQCategory`, `question`, `answer` (rich text), `services` (M2M), `is_published`, `ordering`
**`Feature`** — `name`, `description`, `icon_class`, `link_url`, `block_key` (which section it belongs to), `ordering`, `is_visible`
**`ProcessStep`** — `block_key`, `title`, `description`, `icon_class`, `ordering`
**`TrustBadge`** — `name`, `description`, `image`, `link_url`, `is_visible`, `ordering`

### 7.3 Navigation

**`Navigation`** — `name` (unique: `header`/`mega_menu`/`mobile`/`footer`/`footer_legal`), `is_active`
**`MenuItem`** — `navigation` → `Navigation` (CASCADE), `parent` → self (nullable, CASCADE), `label`, `url` **or** `service` → `Service`, **or** `page` → `Page`, `link_type` (url/service/page/custom_anchor), `icon_class`, `css_classes`, `ordering`, `is_visible`, `open_in_new_tab`, `starts_with` (mobile/desktop visibility)

> Resolves the 11 dead `href="#"` mobile links by construction.

### 7.4 CRM Models

**`Lead`**
`reference` (auto `LEAD-000123`, unique, indexed), `contact` → `Contact` (nullable), `service` → `Service` (nullable, SET_NULL), `name`, `email`, `phone`, `message`, `company_name`
`source` (choices: website_home/website_contact/service_page/phone/referral/paid_ads/organic/other), `source_detail`, `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `gclid`, `landing_page`, `referrer`
`status` (new/contacted/qualified/consultation_scheduled/consultation_held/estimate_sent/estimate_approved/won/lost/archived), `lost_reason`
`priority` (low/normal/high/urgent), `score` (0–100, indexed), `is_hot` (bool, indexed)
`owner` → `User` (nullable, SET_NULL, indexed), `area` → `ServiceArea` (nullable)
`property_address`, `zip_code`, `budget_min`, `budget_max`, `target_start_date`
`consent_marketing` (bool), `consent_timestamp`, `ip_address`, `user_agent`
`first_contacted_at`, `last_contacted_at`, `converted_at`, `estimated_value`
`created_at`, `updated_at`

Indexes: `(status, -created_at)`, `(owner, status)`, `(-score)`, `(service)`, `(area)`, `(-created_at)`

**`Contact`** — `first_name`, `last_name`, `email`, `phone`, `company` → `Company` (nullable), `role`, `address`, `is_vip`, `notes`, `owner` → `User`
**`Company`** — `name`, `website`, `phone`, `address`, `industry`, `notes`
**`LeadActivity`** — `lead` → `Lead` (CASCADE), `activity_type` (note/call/email/sms/meeting/status_change/assignment/system), `summary`, `body`, `direction`, `duration_seconds`, `created_by` → `User`, `created_at`
**`Task`** — `title`, `description`, `lead` → `Lead` (nullable), `assigned_to` → `User`, `due_at`, `priority`, `status` (open/in_progress/done/cancelled), `completed_at`, `created_by`
**`Appointment`** — `lead` → `Lead` (CASCADE), `assigned_to` → `User`, `title`, `start_at`, `end_at`, `timezone`, `location`, `status` (pending/confirmed/completed/cancelled/no_show), `confirmation_sent_at`, `reminder_sent_at`, `notes`
**`Opportunity`** — `lead` → `Lead` (1:1), `name`, `stage` (qualifying/needs_analysis/proposal/negotiation/closed_won/closed_lost), `budget`, `probability`, `expected_close_date`, `owner` → `User`, `won_at`, `lost_reason`
**`Estimate`** — `lead` → `Lead` (CASCADE), `opportunity` (nullable), `number` (unique, auto `EST-2026-0001`), `status` (draft/sent/viewed/approved/declined/expired), `valid_until`, `subtotal`, `tax_amount`, `discount`, `total`, `line_items` (JSONField), `notes`, `public_token` (UUID, for client access), `pdf_file`, `sent_at`, `viewed_at`, `created_by`
**`EmailLog`** — `lead` (nullable), `template` → `EmailTemplate` (nullable), `subject`, `to_address`, `from_address`, `status` (queued/sent/delivered/bounced/failed), `provider_message_id`, `error`, `sent_at`

### 7.5 Platform Models

**`User`** (custom, `AbstractUser`) — `phone`, `role` → `Role` (nullable), `avatar` → `MediaAsset`, `job_title`, `is_staff_member`, `last_login_ip`
**`Role`** — `name`, `slug` (unique), `description`, `permissions` (M2M → `Permission`), `can_view_all_leads` (bool), `default_lead_status`
**`TeamMember`** — `user` → `User` (1:1), `services` (M2M → `Service`, for round-robin), `territories` (M2M → `ServiceAreaGroup`), `weekly_capacity_hours`, `is_accepting_leads`
**`AuditLog`** — `actor` → `User` (nullable, SET_NULL), `action` (create/update/delete/login/publish/export), `content_type`, `object_id`, `object_repr`, `field_changes` (JSON), `ip_address`, `user_agent`, `created_at`. Append-only; no `DELETE` permission.
**`Redirect`** — `from_path` (unique), `to_path`, `status_code` (301/302), `is_active`, `hit_count`, `last_hit_at`
**`ContentVersion`** — `content_type`, `object_id`, `version`, `snapshot` (JSON), `changed_by`, `created_at` — enables rollback
**`SiteFlag`** — `key`, `value` (JSON) — feature toggles, maintenance mode
**`EmailTemplate`** — `key` (unique), `name`, `subject`, `body_html`, `body_text`, `from_address`, `reply_to`, `is_active`
**`Tag`** — `name`, `slug` (unique)
**`ImportJob`** — `kind`, `file`, `status`, `row_count`, `error_count`, `error_log`, `created_by`

### 7.6 Key Relationship Decisions

| Decision | Rationale |
| --- | --- |
| `Lead.service` FK (not a string) | Makes service analytics a JOIN instead of a fuzzy match; unifies the two form contracts |
| `ServiceSection` polymorphic via `section_type` + JSON | One table, one generic template, new section types without migrations in the common case |
| `Project` decoupled from `Service` (M2M) | A single project can appear in multiple service galleries; avoids duplicate rows |
| `MediaAsset` central, referenced by FK | Enables the missing-asset audit, usage tracking, and orphan cleanup |
| `SiteSettings` singleton | Kills 19-file duplication of phone/brand/hours |
| `Redirect` table | Non-negotiable safety net when slugs change under live inbound links |
| Soft delete on content models | Never break a URL that has accumulated SEO equity |
| `AuditLog` append-only | Answers "who changed the phone number and when" for compliance and disputes |

---

## 8. Security & Role Management

### 8.1 Role Matrix

| Permission | Super Admin | Admin | Content Manager | Sales Manager | Estimator | Sales Rep | Viewer |
| --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| View published content | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Edit content / SEO | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Publish / unpublish | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Manage media | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Manage users & roles | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| View all leads | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| View own leads only | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| Create / edit leads | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| Delete leads | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Assign leads | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Manage estimates | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| View financials | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| Manage appointments | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| View audit log | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Site settings | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Export data | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |

Enforced via `Role.permissions` (M2M to `Permission`) + `has_perm` mixin on every `ModelAdmin` + `django-guardian` object-level checks for lead ownership.

### 8.2 Hardening Checklist

**Fix immediately (P0/P1)**
- [ ] Add `python-decouple` → **replace with `django-environ`**; make missing env vars a hard startup failure, not a runtime traceback
- [ ] Remove the D8 store (`bahyamb/`), keep secrets in env only; rotate the exposed `SECRET_KEY`
- [ ] Enforce HTTPS correctly — current `SECURE_SSL_REDIRECT = True` **unconditionally breaks local dev and the cPanel health check**
- [ ] Add `SECURE_PROXY_SSL_HEADER` (Passenger terminates TLS upstream)
- [ ] Replace `SECURE_BROWSER_XSS_FILTER` (removed in Django 4.0) with a real `Content-Security-Policy`
- [ ] `ALLOWED_HOSTS` from env; never hardcode
- [ ] Restrict or remove `/admin/` from public discovery; consider `/crm/` with a `X-Frame-Options: DENY` admin site
- [ ] Serve media from S3/R2 with a signed-URL policy, not from `MEDIA_ROOT` under the webroot

**Authentication**
- [ ] **Mandatory 2FA (TOTP)** for Super Admin, Admin, and Sales Manager — `django-otp-totp` + `django-otp-qrcode`
- [ ] `SessionCookie` expiry 8h, `SESSION_COOKIE_AGE`, `CSRF_COOKIE_HTTPONLY = True`
- [ ] Idle timeout 30 min, absolute 12 h
- [ ] IP allow-list for admin (optional, for the office/VPN)
- [ ] `MAX_LOGIN_ATTEMPTS = 5` + `django-axes` for brute-force lockout
- [ ] No shared accounts; enforce real-name audit attribution
- [ ] Offboarding: deactivate + revoke all sessions immediately

**Input & Data Integrity**
- [ ] Every public form behind a `Form` class with typed fields and real validation (`EmailField`, `RegexField` for phone, `CharField(max_length)`)
- [ ] **hCaptcha or Turnstile on all public forms** — critical for a lead-gen site; honeypot field + IP rate limit as defence in depth
- [ ] `DEFAULT_RENDERING` output escaping is the default — never `|safe` on user input
- [ ] Rich text sanitised server-side (Bleach allowlist) on every save, admin included
- [ ] `DATA_UPLOAD_MAX_MEMORY_SIZE` and `DATA_UPLOAD_MAX_NUMBER_FIELDS` set explicitly
- [ ] File upload validation by MIME **and** magic bytes; strip EXIF (re-embedded GPS is a privacy leak on job-site photos)
- [ ] Parameterised queries only (ORM); no raw SQL in app code
- [ ] DRF: `DEFAULT_THROTTLE_CLASSES` + `DEFAULT_PERMISSION_CLASSES` **set globally**, never per-view opt-in
- [ ] Public write endpoints (lead capture) throttled by IP: 5/hour, 20/day

**Data Protection**
- [ ] Encrypt PII fields at rest where feasible; separate media bucket with restricted access
- [ ] Defined retention policy (e.g. leads 3 years, then purge); automated purge task
- [ ] Full-cookie consent banner + Privacy & Terms pages (currently absent — a live CCPA/GDPR gap given the forms collect name, email, phone, and IP)
- [ ] Encrypted backups, quarterly restore test
- [ ] Documented breach-response runbook

**Logging & Monitoring**
- [ ] `structlog` with request ID; replace all `print()` with structured log records
- [ ] Security events logged: login success/failure, permission denials, bulk export, lead deletion
- [ ] Sentry for exceptions and Celery task failures with alerting
- [ ] Uptime monitor on `/healthz/`
- [ ] Weekly dependency scan; fail CI on high-severity advisories

### 8.3 Public-Site Security

- **CSP** with nonces for the inline scripts currently in `base.html:219-240`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` restricting geolocation/camera
- Pin CDN dependencies (Font Awesome, Google Fonts) with `integrity` + `crossorigin`; self-host fonts to remove third-party requests and improve Core Web Vitals
- Subresource Integrity on any remaining CDN assets
- Rate-limit and monitor `robots.txt`/`sitemap.xml` for scraping

---

## 9. Design Consistency System

*This section exists because design consistency was an explicit requirement, and it spans both the public site and the new CRM.*

### 9.1 Single Source of Truth for Design Tokens

The current `style.css:1-9` already defines a good token set. The problem is that the other ~600 lines of CSS live in **per-page inline `<style>` blocks** (`kitchen.html:5-126` alone is 120 lines), and 2 of 3 referenced stylesheets don't exist. The fix is to centralise everything.

```css
/* core/static/css/tokens.css — the only place design values are defined */
:root {
  /* Brand */
  --primary:      #1B3022;   /* deep forest green */
  --accent:       #E27D60;   /* terracotta */
  --accent-dark:  #D16B4F;
  --dark:         #2D2D2D;
  --light:        #F4F4F2;
  --white:        #ffffff;

  /* Type scale */
  --font-main:    'Montserrat', sans-serif;
  --font-heading: 'Playfair Display', serif;
  --fs-xs: 0.875rem;  --fs-sm: 1rem;     --fs-base: 1.0625rem;
  --fs-lg: 1.25rem;   --fs-xl: 1.5rem;   --fs-2xl: 2rem;
  --fs-3xl: 2.5rem;   --fs-4xl: 3.5rem;  --fs-5xl: clamp(3rem, 7vw, 5rem);

  /* Spacing scale (4px base) */
  --sp-1: 4px;  --sp-2: 8px;   --sp-3: 12px;  --sp-4: 16px;
  --sp-5: 24px; --sp-6: 32px;  --sp-8: 48px;  --sp-10: 64px;
  --sp-12: 80px; --sp-16: 120px;

  /* Radii, shadows, transitions, breakpoints */
  --radius-sm: 5px;  --radius-md: 15px;  --radius-lg: 20px;  --radius-xl: 30px;
  --shadow-sm: 0 2px 15px rgba(0,0,0,.05);
  --shadow-md: 0 10px 30px rgba(0,0,0,.10);
  --shadow-offset: 20px 20px 0 var(--light);
  --transition: all .5s cubic-bezier(.23, 1, .32, 1);
}
```

**Rules enforced in review:**

1. **No hardcoded hex values in templates or page CSS.** Only `var(--token)`. Enforceable with a stylelint rule (`color-no-hex-literals`) in CI.
2. **No `<style>` blocks in page templates.** Page-specific CSS moves to a component stylesheet keyed by section type (`sections/_gallery.css`, `_hero.css`). This is the mechanism that guarantees a `cta` section restyled once updates all 15 pages.
3. **No new section class names without a corresponding component.** The 20+ gallery/grid/card class names currently in use (`gallery-item`, `masonry-item`, `tile-card`, `step-card`, `shed-item`, `pattern-card`, `eng-card`, `condo-card`, `cabinet-item`, `usage-card`, `precision-card`, `detail-card`, `danger-item`, `compliance-card`, `cert-item`, `material-item`, `spec-item`, `feature-card`, `service-nav-item`, `stat-item`) collapse into a small set: `.card`, `.card__media`, `.card__body`, `.card__title`, `.card__text`, with modifiers for grid span and ratio.
4. **Layout primitives:** `.container`, `.grid`, `.stack`, `.cluster` (from a small utility set) instead of ad-hoc `grid-template-columns` per page.

### 9.2 Component Library

Components are Django partials in `templates/partials/`, each consuming only tokens. One component serves all pages.

| Component | Replaces |
| --- | --- |
| `partials/nav.html` | Hardcoded mega-menu (15 `<li>`) + broken mobile menu |
| `partials/footer.html` | 4 hardcoded footer columns |
| `partials/seo.html` | Inline meta + JSON-LD in `base.html:8-71` |
| `partials/breadcrumbs.html` | *(new — required for SEO)* |
| `partials/lead_form.html` | 2 divergent form implementations |
| `partials/section_*.html` | 19 pages × bespoke markup |
| `partials/card.html` | 20+ bespoke card classes |
| `partials/cta_banner.html` | 15 copies of the CTA banner |
| `partials/pagination.html` | *(new — for galleries)* |

### 9.3 CRM Visual Consistency

The CRM must feel like the same product. `django-unfold` is themable directly from these tokens:

```python
# users/roles.py  (conceptual)
UNFOLD = {
    "SITE_TITLE": "Integrity Home Improvements — CRM",
    "SITE_HEADER": "logo.svg",
    "SITE_SYMBOL": "logo-mark.svg",
    "COLORS": {
        1: "#1B3022",   # primary  → nav sidebar
        2: "#E27D60",   # accent   → active nav, buttons
        3: "#1B3022",   # success
        4: "#E27D60",   # warning
    },
    "SIDEBAR": {"show_search": True, "show_social": False},
    "THEME": "light",
}
```

Additionally:

- **Brand logo + favicon** in the admin header, sourced from `SiteSettings.logo` — upload once, reflected site-wide and in CRM
- **Custom CSS layer** (`crm/static/crm/admin-theme.css`) importing the same `tokens.css`, so CRM and public site cannot drift
- **`ModelAdmin` templates** for pipeline cards and dashboards that reuse the site's card and badge classes
- **Empty states, help text, and form labels** written in plain language for a non-technical marketing and sales audience — e.g. "SEO Description — the summary Google shows under your link (max 160 characters)"
- **Inline help throughout:** every field explains what it does and where it appears on the site
- **Responsive admin:** staff can review and action leads from a phone — essential for a field sales team

### 9.4 Brand Governance

The CRM makes the three-identity problem *structurally impossible* to reintroduce:

- `SiteSettings` is the only source for name, phone, address, email, hours, social
- `base.html` reads all of it from the context processor
- A **brand consistency test** fails CI if any hardcoded phone number, email, or company name remains in a template:

```python
# tests/test_brand_consistency.py
FORBIDDEN = [r"\(?443\)?[-. ]?898[-. ]?3143", r"agm@rlecd\.com",
             r"REAL LIFE EXPERIENCE", r"Reisterstown"]
def test_no_hardcoded_brand_data():
    for tpl in Path("templates").rglob("*.html"):
        text = tpl.read_text()
        for pattern in FORBIDDEN:
            assert not re.search(pattern, text), f"{tpl} hardcodes {pattern}"
```

A single editorial decision (update `SiteSettings`) now propagates to the nav, footer, contact page, JSON-LD, and both forms simultaneously.

---

## 10. Scalability & Maintainability

### 10.1 Performance Targets

| Metric | Target |
| --- | --- |
| Public page LCP (75th pct, mobile) | < 2.5 s |
| Public page TTFB | < 200 ms (cache hit < 50 ms) |
| CRM list view (1,000 leads) | < 500 ms |
| CRM dashboard | < 800 ms |
| Lead form submission | < 1 s to ack (email async) |
| Error rate | < 0.5% |

### 10.2 Caching Strategy

| Layer | Key | TTL | Invalidation |
| --- | --- | --- | --- |
| Full-page | `page:home`, `page:service:<slug>` | 6 h | `post_save`/`post_delete` on relevant models |
| Fragment | `nav:header`, `site:settings` | 24 h | `SiteSettings` save |
| Template | Django `cached.Loader` | — | `template_changed` signal |
| Query | Redis via `django-redis` | 5–15 min | Manual per-view |
| Media | Cloudflare CDN | 1 y, immutable | URL versioning on replace |

Invalidation is signal-driven — a `Service` save busts exactly its own page key, not the whole cache.

### 10.3 Database Practices

- **PostgreSQL from day one.** SQLite cannot satisfy concurrent write volume or `JSONB`/full-text needs.
- Index every FK used for filtering; composite indexes on `(status, -created_at)`, `(owner, status)`, `(-score)`
- `select_related` for FKs and `prefetch_related` for M2M/reverse — mandatory on any list page with related data
- Enforce `.only()`/`.defer()` on heavy admin changelists
- Cursor pagination (keyset) for leads and galleries; never OFFSET beyond page 3
- `connection pooling` via PgBouncer when the process count grows
- `pg_dump` nightly + WAL archiving / PITR; monthly restore drill
- Read replica deferred until measured need

### 10.4 Frontend Performance

- **Convert all 103 images to WebP + AVIF** with `srcset` and explicit `width`/`height` (CLS ≈ 0)
- **Self-host fonts** (removes two third-party origins); `font-display: swap`; preload the two families actually used
- **Defer non-critical JS**; the 67-line carousel needs no framework
- **Remove the `background-attachment: fixed`** used in service-page heroes — a known mobile-performance killer
- Lazy-load below-fold images, `fetchpriority="high"` on the LCP hero
- **Self-host Font Awesome** (or subset to used glyphs) — 99 files shipped for ~50 icons
- Prune the 2 missing stylesheet references and any orphaned CSS

### 10.5 Maintainability Practices

**Architecture**
- Strict app boundaries: `content` never imports from `crm`; cross-app access via service-layer functions
- No `import *` — the current `from .views import *` is banned
- Thin views, fat models, explicit service layer for business logic
- Named app namespaces (`crm:leads`) and URL reversing everywhere

**Code Quality**
- `ruff` + `black`, enforced in CI
- `settings/` split into `base.py` / `dev.py` / `prod.py` / `test.py`; the current single file mixes both
- `mypy --strict` on the domain layer
- **Pre-commit hooks** on every commit
- `django-upgrade` to keep the 3.10→3.12 path clean

**Testing**
- `pytest-django` + `factory_boy`; **80% coverage on `core`/`content`/`crm`**
- Unit: models, slug generation, section payload validation, lead scoring, role permissions
- Integration: lead capture end-to-end (form → DB → activity → email queued), publishing workflow, media upload
- Regression: **visual-regression snapshots** of all 19 public pages before and after the refactor — the single most important safety net for a redesign of this size
- Smoke tests on deploy

**Documentation**
- ADRs (`docs/adr/`) for every significant decision — especially the section-block choice and the `django-unfold` choice
- Data dictionary auto-generated from model docstrings
- Runbook: deploy, rollback, restore, incident response, key rotation
- Onboarding guide for non-technical staff

### 10.6 Technical Debt Burn-Down

| Item | Effort | Phase |
| --- | --- | --- |
| Replace `python-decouple` | 0.5 d | 0 |
| Remove 2 dead stylesheet refs | 0.25 d | 0 |
| Add 14 missing `{% block title %}` | 0.5 d | 0 |
| Fix `additions.html` condo content | 1 d | 0 |
| Fix 11 dead mobile nav links | 0.5 d | 0 |
| Resolve 3-brand-identity conflict | 2 d | 0 (decision) / Phase 1 (build) |
| Replace SQLite with PostgreSQL | 1 d | 1 |
| Split settings; harden security | 2 d | 1 |
| Extract partials; centralise CSS | 5 d | 2 |
| Zero `print()` → `structlog` | 1 d | 1 |
| Add error pages (404/500/503) | 0.5 d | 0 |
| Add sitemap/robots/favicon | 1 d | 1 |
| Add privacy/terms pages | 0.5 d | 0 |
| Add health-check endpoint | 0.25 d | 1 |

---

## 11. Deployment & Maintenance

### 11.1 Current Reality

- **Host:** cPanel, shared/VPS, Phusion Passenger
- **Paths:** hardcoded absolute paths in `passenger_wsgi.py` (`/home/hightech/...`)
- **Python:** 3.10 (EOL Oct 2026 — must upgrade)
- **Django:** 5.2.13 pinned; `settings.py` docstring claims 6.0.4 (inconsistent)
- **State:** `staticfiles/` and `db.sqlite3` are committed to the working tree
- **Not a git repo** — no version control, no CI, no rollback. **This is the highest-risk item in the entire engagement.**

### 11.2 Target Deployment Architecture

```
        GitHub (private repo)  ← source of truth
              │
      ┌───────┴────────┐
      │ GitHub Actions │
      │  test → lint   │
      │  → build →     │
      │    deploy      │
      └───────┬────────┘
              │ (SSH / FTP over TLS)
              ▼
   cPanel + Passenger
   ┌──────────────────────────┐
   │ app/  (code, venv)       │
   │ .env  (secrets — 600)    │
   │ media/ → S3/R2 (no local)│
   └──────────┬───────────────┘
              │
   ┌──────────▼───────────────┐
   │ Managed PostgreSQL 16    │
   │ Managed Redis 7          │
   └──────────────────────────┘
```

**Environment-specific settings** (fixing the current single-file mix):
`settings/base.py` (shared) · `settings/dev.py` (DEBUG on, SQLite allowed) · `settings/prod.py` (DEBUG off, security hardening on) · `settings/test.py` (fast, in-memory). Selected via `DJANGO_SETTINGS_MODULE`.

**Secrets:** all in `.env` (mode `600`, gitignored, never committed). `django-environ` validates types and **fails fast at startup** on a missing variable — turning the current silent 500 into a clear, immediate error.

**Pre/post-deploy hook:**
```bash
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear
# restart Passenger application via tmp/restart.txt (already scaffolded in repo)
```

**Zero-downtime:** Passenger hot-restarts on file change; `migrate` runs against a maintenance-mode guard for the brief schema window; database migrations are backwards-compatible (expand → migrate → contract) so no rollback requires a schema revert.

### 11.3 CI/CD Pipeline

```
on: push / pull_request

1. Install deps (cache pip)
2. ruff check + black --check        → fail on lint
3. mypy --strict (domain layer)      → fail on type error
4. Bandit security scan              → fail on high severity
5. pytest -m "not e2e" --cov --cov-fail-under=80
6. Visual regression diff            → report on change
7. Build collectstatic + collectmedia
8. Deploy to staging → smoke tests
9. Deploy to production (on merge to main)
```

### 11.4 Backup & Disaster Recovery

| Asset | Method | Frequency | Retention | RPO | RTO |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL | Automated managed backups + WAL/PITR | Continuous | 7 daily / 4 weekly / 6 monthly | < 5 min | < 1 h |
| Media (S3/R2) | Versioning + cross-region replication | Continuous | Indefinite | ~0 | < 4 h |
| Code | GitHub private repo, tagged releases | Per deploy | Indefinite | ~0 | < 15 min |
| `.env` | Encrypted password manager (1Password/Bitwarden) | On change | Indefinite | — | < 15 min |
| Config | Git (no secrets) | Per change | Indefinite | — | < 15 min |

**Tested quarterly.** An untested backup is not a backup.

### 11.5 Observability

- **Sentry** — Django, Celery, and template errors; alerts to a shared inbox
- **Uptime monitor** on `/healthz/` (1-minute interval) from an external region
- **Structured logs** (`structlog`, JSON in prod) shipped to a central sink; request IDs correlate a lead's form POST to its Celery email job
- **PostgreSQL slow-query log** with alerting on regressions
- **Celery Flower** for queue depth and failed-task inspection
- **UptimeCore/Synthetics** on the top 5 pages plus a synthetic lead submission

### 11.6 Maintenance Cadence

| Cadence | Activity |
| --- | --- |
| **Daily** | Automated backup verification; error-alert triage; new-lead review |
| **Weekly** | Lead pipeline review and follow-up on stale leads; broken-link and missing-media audit; dependency update PRs |
| **Monthly** | Content review with the client; Core Web Vitals check (Lighthouse/CrUX); `pip list --outdated` and security-advisory review; database `ANALYZE`; restore spot-check |
| **Quarterly** | **Tested disaster-recovery drill**; access review (user roles, offboarding); penetration test or security scan; performance budget review |
| **Annually** | Python/Django upgrade path review; TLS/certificate check; content and brand audit; SEO strategy review; capacity and cost review |

### 11.7 Backup Plan (Vendor Lock-In)

Full source and data are portable: PostgreSQL, S3 (or R2, S3-compatible), `python-decouple`/`django-environ`-managed env, plain Django. A `BACKUP.md` documents the full restore procedure, verified quarterly. The client owns the GitHub repo and the database — no proprietary lock-in, and `django-unfold` is a theme, not a platform.

---

## 12. Delivery Roadmap

### Phase 0 — Emergency Stabilisation (Week 1)
**Goal: the site is reliable, secure, and correct. No new features.**

- [ ] Initialise a private Git repository; first commit of current state
- [ ] Fix `python-decouple`; move secrets to `.env`; rotate `SECRET_KEY`
- [ ] Make `SECURE_SSL_REDIRECT` environment-aware
- [ ] Remove dead `SECURE_BROWSER_XSS_FILTER`; delete 2 missing stylesheet references
- [ ] Add 14 missing `{% block title %}` blocks
- [ ] Fix `additions.html` (condo → additions) — or rename the route deliberately
- [ ] Fix 11 dead `href="#"` mobile nav links
- [ ] Add `404.html`, `500.html`, `favicon.ico`
- [ ] Add privacy policy and terms pages
- [ ] Add hCaptcha + rate limiting to both forms
- [ ] Persist leads to a temporary `Lead` model **immediately** (stop the bleeding on lost inquiries)
- [ ] Add `SERVICE = lead-service` to cPanel so Passenger restarts on code changes
- [ ] Add `/healthz/` endpoint

**Exit criteria:** site boots reliably, no P0 defects, no lead is lost.

### Phase 1 — Foundation (Weeks 2–4)
**Goal: the platform the CRM will be built on.**

- [ ] Split settings into base/dev/prod/test
- [ ] Migrate SQLite → PostgreSQL; data migration script
- [ ] Install Redis, Celery, WhiteNoise, `django-environ`, `django-storages`
- [ ] Configure S3/R2 media storage + CDN
- [ ] Standardise `structlog`; remove all `print()`
- [ ] Tokenise CSS into `tokens.css`; remove inline `<style>` from templates
- [ ] Extract partials: `nav`, `footer`, `seo`, `lead_form`, `card`
- [ ] Rewrite `base.html` against the context processor
- [ ] `django-sitemap` + `robots.txt`
- [ ] CI pipeline: lint, type, test, coverage gate
- [ ] `core` app: `SiteSettings`, `MediaAsset`, `Navigation`, `Redirect`, `AuditLog`
- [ ] Custom `User` + `Role` + `TeamMember`; seed roles from the matrix
- [ ] `django-unfold` installed and themed to brand tokens

**Exit criteria:** all shell data flows from the database; CI green; PostgreSQL live.

### Phase 2 — Content Platform (Weeks 5–8)
**Goal: marketing manages all content without a developer.**

- [ ] `content` app models: `Service`, `ServiceCategory`, `Section`, `Page`, `Project`, `ServiceArea(+Group)`, `FAQ`, `Testimonial`, `Feature`, `ProcessStep`, `TrustBadge`, `EmailTemplate`
- [ ] Section registry + generic renderer
- [ ] `service_detail.html` generic template
- [ ] `api/`: DRF serializers, viewsets, routers, throttles; public read API + lead write endpoint
- [ ] Media library: upload, focal point, alt-text enforcement, WebP/AVIF pipeline, missing-asset audit
- [ ] **Content migration:** script all 15 services, ~100 projects, 40 service areas, home sections from existing templates, with a verification report
- [ ] SEO rebuild: per-entity meta, `seo_head`/`jsonld` tags, `Service`/`FAQPage`/`BreadcrumbList` schema
- [ ] Site-wide page caching + signal-driven invalidation
- [ ] Visual regression baseline for all 19 pages

**Exit criteria:** a non-technical user can edit any page section, add a service, and upload a project — live.

### Phase 3 — Lead CRM (Weeks 9–12)
**Goal: the business manages its pipeline.**

- [ ] `crm` app models: `Lead`, `Contact`, `Company`, `LeadActivity`, `Task`, `Appointment`, `Opportunity`, `Estimate`, `EmailLog`
- [ ] Shared `capture_lead` service; both forms rewritten against it; unified `service` FK contract
- [ ] Celery email pipeline with retry, dead-letter, and `EmailTemplate` rendering
- [ ] **Custom CRM admin:** lead pipeline (status/owner/score/priority list pages, annotated KPIs), kanban by status, bulk actions, custom filters, lead detail with full activity timeline
- [ ] Lead scoring rules + auto hot-flagging
- [ ] Round-robin assignment by `TeamMember` service/territory
- [ ] Deduplication on email/phone + merge flow
- [ ] Appointment scheduling with timezone correctness
- [ ] Estimate builder + branded PDF
- [ ] Task/follow-up module
- [ ] Analytics dashboard
- [ ] `AuditLog` wired to all mutations

**Exit criteria:** a lead submitted at 2pm is in the pipeline with an owner, an activity trail, and no possibility of being lost.

### Phase 4 — Growth & Optimisation (Weeks 13–16)
**Goal: measurable business improvement.**

- [ ] Per-service-area landing pages (programmatic SEO on the 40-city list)
- [ ] FAQ pages + `FAQPage` schema site-wide
- [ ] Testimonials module live on service pages + `AggregateRating`
- [ ] GA4 + Search Console integration; conversion funnel tracking
- [ ] Image optimisation pass; Core Web Vitals to green
- [ ] Full accessibility audit (WCAG 2.1 AA) and remediation
- [ ] Automated SEO health report (missing titles/images, broken links, redirect hits)
- [ ] Calendar/agenda view
- [ ] Staff training sessions + runbook handover

**Exit criteria:** SEO and performance measurably improved; team fully self-sufficient.

---

## 13. Effort Estimation & Resourcing

| Phase | Duration | Senior Dev | Notes |
| --- | --- | --- | --- |
| Phase 0 — Stabilisation | 1 week | 1.0 | Mostly small, high-impact fixes |
| Phase 1 — Foundation | 3 weeks | 1.0 | Settings, infra, CI, shell refactor |
| Phase 2 — Content Platform | 4 weeks | 1.0–1.5 | Largest build; includes migration |
| Phase 3 — Lead CRM | 4 weeks | 1.0 | Admin customisation is the bulk |
| Phase 4 — Growth & Optimisation | 4 weeks | 0.5 | Includes training |
| **Total** | **16 weeks** | **~1.2 FTE avg** | **Single senior engineer** |

**Assumptions:** one senior Django engineer; client provides content, copy, photography, and brand decisions; access to hosting, DNS, Google Business Profile, and existing analytics; content migration is from existing templates only; no e-commerce, no customer portal, no mobile app.

**Exclusions:** paid advertising, photography/videography, custom illustration, logo/brand identity work, copywriting, third-party licence fees, and native mobile apps.

**Change-control:** scope beyond this plan (customer portal, quoting engine with e-sign, QuickBooks integration, SMS campaigns) is estimated separately.

---

## 14. Risks & Assumptions

### 14.1 Risks

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | **No version control today** — the live site is unprotected against an accidental overwrite | High | Critical | Phase 0, Day 1: `git init`, commit, push to a private remote before any edit |
| R2 | **99 missing images** — the site is visually broken now | Confirmed | High | Audit and source the real assets before Phase 2 migration; treat image acquisition as a client dependency with a dated deadline |
| R3 | **Brand/geography ambiguity** — is this an Atlanta or Maryland business? Three names in play | High | High | Blocking decision in Phase 0. Everything else (SEO, schema, phone) depends on it. Do not build on an unverified identity |
| R4 | **cPanel/shared-hosting limits** — no container runtime, possibly no managed PostgreSQL/Redis | Medium | High | Verify in Phase 0. Fallbacks: managed Postgres (Railway/Neon), Upstash Redis, and a small VPS with Docker if Passenger is genuinely constraining |
| R5 | **`additions.html` content mismatch** may indicate other silent template drift | Medium | Medium | Full content audit in Phase 0; visual regression baseline in Phase 2 |
| R6 | **Scope creep** — a "CRM" invites requests for quoting, invoicing, and payroll | High | Medium | This plan is explicit about what is and is not included (§13). Additions are change-control items |
| R7 | **Content migration is manual and error-prone** across 15 pages and ~100 assets | High | Medium | Automate the extraction; verify with visual regression and a per-page checklist; require client sign-off per page |
| R8 | **Python 3.10 EOL (Oct 2026)** | Certain | Medium | Upgrade to 3.12 in Phase 1, before deep CRM work |
| R9 | **Non-technical staff adoption** — the CRM is worthless if unused | Medium | High | Plain-language labels, inline help, minimal required fields, and Phase 4 training. Design the UX for a marketing manager, not a developer |
| R10 | **Lead-volume growth overwhelms SQLite** | Low (today) | Critical | PostgreSQL in Phase 1 — not deferred |

### 14.2 Assumptions

1. The single Django application continues to be the platform of record.
2. Content is text + images; no video hosting is required.
3. "CRM" means **lead and pipeline management** — not accounting, payroll, or field-service scheduling.
4. The client will supply and own the photography; the CRM manages it but does not produce it.
5. cPanel/Passenger remains the deployment target for the public site. The CRM ships inside the same Django project unless a separate host is preferred (the API-first design supports splitting later).
6. A single senior engineer is available for ~16 weeks.
7. A brand/geography decision (§14.1 R3) is made before Phase 1 begins.

### 14.3 Client Dependencies (blocking)

| # | Dependency | Needed By |
| --- | --- | --- |
| D1 | **Brand + geography decision** (final company name, service region, phone, address) | End of Phase 0 |
| D2 | All photography — ~100 project images + 15 heroes + logo + favicon + OG image | Start of Phase 2 |
| D3 | cPanel/DNS/analytics access + confirmation on managed Postgres/Redis availability | Start of Phase 1 |
| D4 | A named decision-maker for content sign-off and training | Start of Phase 2 |
| D5 | Any historical leads to import | Start of Phase 3 |

---

## Appendix A — Current Content Inventory

### A.1 Service Pages (15 routes, 19 templates)

| # | URL | View | Template | Title Block | Gallery Items | Issue |
| --- | --- | --- | --- | :---: | --- | --- |
| 1 | `/` | `index` | `index.html` | ✅ | — | Mixed brand identity |
| 2 | `/about/` | `about` | `about.html` | ✅ | — | "21+ Years" vs 18 elsewhere |
| 3 | `/areas-we-serve/` | `areas_we_serve` | `areas_we_serve.html` | ✅ | — | Two conflicting area lists |
| 4 | `/contact/` | `contact` | `contact.html` | ✅ | — | Only 4 of 15 services |
| 5 | `/bathroom-remodeling/` | `bathroom_remodeling` | `bathroom.html` | ❌ | 20 | No intro/features; thin |
| 6 | `/kitchen-remodeling/` | `kitchen_remodeling` | `kitchen.html` | ❌ | 6 | Reference template |
| 7 | `/basement-finishing/` | `basement_finishing` | `basement.html` | ❌ | 5 | — |
| 8 | `/home-additions/` | `home_additions` | `additions.html` | ❌ | 3 | **Condo content** |
| 9 | `/painting/` | `painting` | `painting.html` | ❌ | 3 | — |
| 10 | `/home-improvement/` | `home_improvement` | `home_improvement.html` | ❌ | — | Duplicates nav |
| 11 | `/patios-decks/` | `patios_decks` | `patios_decks.html` | ❌ | 3 | — |
| 12 | `/cabinets/` | `cabinets` | `cabinets.html` | ❌ | 4 | — |
| 13 | `/woodworking/` | `woodworking` | `woodworking.html` | ❌ | 4 | — |
| 14 | `/hardscaping/` | `hardscaping` | `hardscaping.html` | ❌ | 5 | — |
| 15 | `/walkway-designs/` | `walkway_designs` | `walkways.html` | ❌ | 2 | — |
| 16 | `/pergolas/` | `pergolas` | `pergolas.html` | ❌ | 6 | — |
| 17 | `/lead-removal/` | `lead_removal` | `lead_removal.html` | ❌ | 2 | 4-step process |
| 18 | `/shed-builder/` | `shed_builder` | `sheds.html` | ❌ | 4 | — |
| 19 | `/lead-renovator/` | `lead_renovator` | `lead_renovator.html` | ❌ | 2 | EPA cert content |

### A.2 Service Page Anatomy (canonical pattern)

Every service page follows this structure with unique class names and assets:

```
<section class="hero-{service}">           eyebrow · <h1> · subtitle · [hero image]
[optional] <section class="{x}-bar">        trust / cert / spec strip
<section class="{x}-intro|features|detail">
    <h2>                                    section heading
    <h3> × 3–5                             feature cards (icon + title + body)
<section class="{x}-gallery">               <h2> + gallery items (image + title + caption)
[optional] <section class="process|steps">  <h3> × 4                 process steps
[optional] <section class="stats">          trust statistics
<section class="cta-banner|bottom-cta">     headline + body + CTA button
```

Because the *structure* is consistent while the *markup* diverges, the Section model (§7.2) captures it losslessly.

### A.3 Asset Gap

| Category | Referenced | Present | Missing |
| --- | --- | --- | --- |
| Images | 103 | 4 | **99** |
| CSS files | 3 | 1 | **2** |
| JS files | 1 | 1 | 0 |
| Fonts | 2 (CDN) | 0 | Self-host recommended |
| Favicon | 1 | 0 | 1 |
| OG share image | 1 | 0 | 1 |
| Logo (JSON-LD) | 1 | 0 | 1 |

### A.4 Content Modules Required (summary of §7.2)

| Content type | Current source | Target model | Count |
| --- | --- | --- | --- |
| Services | 15 hardcoded URLs | `Service` | 15 |
| Service categories | Implicit nav groups | `ServiceCategory` | 4 |
| Projects/gallery images | `{% static %}` paths | `Project` + `MediaAsset` | ~100 |
| Service areas | 2 hardcoded lists | `ServiceArea` + `ServiceAreaGroup` | 40 |
| Homepage sections | 9 inline sections | `Section` (`block_key`) | 9 |
| Feature/value cards | Inline HTML | `Feature` | 11 |
| Process steps | Inline HTML | `ProcessStep` | 4 |
| Trust badges | Inline HTML | `TrustBadge` | 3 |
| Testimonials | **None** | `Testimonial` | 0 → new |
| FAQs | **None** | `FAQ` | 0 → new |
| Navigation | 2 hardcoded menus | `Navigation` + `MenuItem` | 3 |
| Contact details | 19 files, 3 brands | `SiteSettings` | 1 |
| Email copy | f-strings in views | `EmailTemplate` | 2 |
| Leads | **Discarded** | `Lead` | ∞ |

---

*End of plan. Prepared for client review and approval. Phase 0 can begin immediately on approval; Dependency D1 (brand/geography decision) is the only blocking input.*
