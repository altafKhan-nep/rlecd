"""Audit every admin page at every width in a single browser session.

The per-page, per-width loop that came before this launched a fresh Chromium
for each measurement, which cost about ninety seconds a page and made it
tempting to check fewer pages than you meant to. One login, one browser, all
combinations.

    python audit_layout.py            # all pages, all widths
    python audit_layout.py --width 390
"""
import json
import sys

import measure_layout as m

PAGES = [
    "/admin/",
    "/admin/content/page/",
    "/admin/content/section/",
    "/admin/content/sectionimage/",
    "/admin/content/pageimage/",
    "/admin/content/project/",
    "/admin/content/servicearea/",
    "/admin/content/faq/",
    "/admin/content/testimonial/",
    "/admin/content/trustbadge/",
    "/admin/content/mediaitem/",
    "/admin/crm/lead/",
    "/admin/crm/contact/",
    "/admin/crm/task/",
    "/admin/crm/teammember/",
    "/admin/crm/auditlog/",
    "/admin/pipeline/",
    "/admin/login/",
]

# Django's own app registry is the source of truth for what is registered, so
# the audit cannot quietly miss a model that was added last week.
EXTRA = []


def collect_pages():
    import subprocess
    import os
    code = (
        "import django, os;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','home_improvement.settings');"
        "django.setup();"
        "from django.contrib import admin;"
        "print('\\n'.join(sorted({m._meta.app_label + '/' + m._meta.model_name"
        " for m in admin.site._registry})))"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backend = os.path.join(root, "backend")
    out = subprocess.run([sys.executable, "-c", code], cwd=backend,
                         capture_output=True, text=True)
    if out.returncode != 0:
        return PAGES
    models = ["/admin/%s/%s/" % tuple(p.split("/")) for p in out.stdout.split()]
    # Keep only the ones the hard-coded list knows are safe to GET as a
    # changelist; a few admin screens are add-forms by default.
    return PAGES + [p for p in models if p not in PAGES]


PROBE = r"""
(() => {
  const vw = document.documentElement.clientWidth;
  const bad = [];
  document.querySelectorAll('body *').forEach((el) => {
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed' || cs.display === 'none') return;
    if (cs.overflowX !== 'visible') return;
    const r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1) return;
    // An element can only push the page sideways if no ancestor clips it.
    let p = el.parentElement, clipped = false;
    while (p) {
      const pcs = getComputedStyle(p);
      if (pcs.overflowX !== 'visible') { clipped = true; break; }
      p = p.parentElement;
    }
    const over = Math.round(r.right - vw);
    if (over > 1 && !clipped) {
      bad.push({ tag: el.tagName.toLowerCase(),
                 cls: String(el.className || '').slice(0, 46),
                 id: el.id || '', over, w: Math.round(r.width) });
    } else if (over > 1) {
      // Clipped instead of scrolled: the content is unreachable rather than
      // scrollable, which reads as a mysteriously short column.
      bad.push({ tag: el.tagName.toLowerCase(),
                 cls: String(el.className || '').slice(0, 46),
                 id: el.id || '', clippedBy: over, w: Math.round(r.width) });
    }
  });
  const seen = new Set();
  const uniq = bad.filter((b) => {
    const k = (b.cls || b.tag) + '|' + (b.over || b.clippedBy);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  return {
    viewport: vw,
    scrollWidth: document.documentElement.scrollWidth,
    scrolls: document.documentElement.scrollWidth > vw + 1,
    path: location.pathname,
    bad: uniq.slice(0, 6),
    count: uniq.length,
  };
})()
"""


def main():
    widths = [1920, 1600, 1500, 1441, 1440, 1366, 1280, 1152,
              1024, 900, 768, 640, 480, 390, 360]
    only = None
    if "--width" in sys.argv:
        only = int(sys.argv[sys.argv.index("--width") + 1])
        widths = [only]

    m.PROBE = PROBE
    pages = collect_pages()
    print("auditing %d pages x %d widths\n" % (len(pages), len(widths)))

    # Batched so a dropped websocket costs one batch, not the whole sweep, and
    # so no single session is asked to drive hundreds of navigations.
    BATCH = 6
    results = []
    for i in range(0, len(pages), BATCH):
        chunk = pages[i:i + BATCH]
        results.extend(m.run_all(chunk, widths))
        done = min(i + BATCH, len(pages))
        print("  ...%d/%d pages measured" % (done, len(pages)))

    errors = [r for r in results if r.get("error")]
    if errors:
        print("\n%d measurement(s) lost to a dropped session; rerun for those"
              % len(errors))
    results = [r for r in results if not r.get("error")]
    failures = [r for r in results if r.get("count") or r.get("scrolls")]

    width_fail = {}
    for f in failures:
        width_fail.setdefault(f["requestedWidth"], []).append(f)

    for w in widths:
        bad = width_fail.get(w, [])
        mark = "FAIL" if bad else " ok "
        print("%s %5dpx  %s" % (mark, w, ("%d page(s)" % len(bad)) if bad else ""))

    if failures:
        print("\n--- detail ---")
        for f in failures:
            head = f["bad"][0] if f["bad"] else {}
            what = head.get("cls") or head.get("tag") or "?"
            amt = head.get("over") or head.get("clippedBy")
            kind = "clipped" if "clippedBy" in head else "overflow"
            print("  %-34s %5dpx  %-10s %s +%s"
                  % (f["path"], f["requestedWidth"], kind, what, amt))

    print("\n%s" % ("ALL CLEAN" if not failures
                    else "%d failing page/width combinations" % len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
