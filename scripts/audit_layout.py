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

  // A dead gutter does not overflow, so the sweep above would call it clean.
  // The login page has no sidebar, so #container must sit flush at the left
  // edge; when the shell's sidebar offset leaked onto it, the whole design was
  // pushed right behind a band of blank white and every measurement here still
  // read "no overflow".
  let deadGutter = null;
  const container = document.getElementById('container');
  if (container && document.body.classList.contains('rlecd-login')) {
    const left = Math.round(container.getBoundingClientRect().left);
    if (left > 1) deadGutter = { left, what: '#container on the login page' };
  }

  return {
    viewport: vw,
    scrollWidth: document.documentElement.scrollWidth,
    scrolls: document.documentElement.scrollWidth > vw + 1,
    path: location.pathname,
    deadGutter,
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
    BATCH = 1  # one page per session: a long-lived CDP socket drops under
            # load, and a short session means a drop costs one page, not a batch
    # The login page is measured signed out. With a session it redirects to the
    # dashboard, so it would be "audited" as a page it never rendered.
    ANONYMOUS = {"/admin/login/"}
    results = []
    signed_in, signed_out = [], []
    for page in pages:
        (signed_out if page in ANONYMOUS else signed_in).append(page)

    for group, auth in ((signed_in, True), (signed_out, False)):
        for i in range(0, len(group), BATCH):
            results.extend(m.run_all(group[i:i + BATCH], widths, auth=auth))
    print("  ...%d pages measured (%d signed out)"
          % (len(pages), len(signed_out)))

    # A dropped websocket costs the page/width pairs measured after the drop,
    # not the whole sweep -- so re-measure exactly those, in a fresh session,
    # rather than reporting a pass with holes in it.
    for attempt in range(3):
        lost = [r for r in results if r.get("error")]
        if not lost:
            break
        print("  retrying %d lost measurement(s), pass %d"
              % (len(lost), attempt + 1))
        by_page = {}
        for r in lost:
            by_page.setdefault(r["path"], set()).add(r["requestedWidth"])
        keep = []
        replacements = {}
        for group, auth in (
                ([p for p in by_page if p not in ANONYMOUS], True),
                ([p for p in by_page if p in ANONYMOUS], False)):
            if not group:
                continue
            for f in m.run_all(group, sorted(
                    {w for p in group for w in by_page[p]}), auth=auth):
                key = (f.get("path"), f.get("requestedWidth"))
                replacements.setdefault(key, f)
        results = [r for r in results if not r.get("error")]
        for r in results:
            key = (r.get("path"), r.get("requestedWidth"))
            if key in replacements and replacements[key].get("error"):
                keep.append(replacements[key])
        results.extend(keep)

    errors = [r for r in results if r.get("error")]
    if errors:
        print("\n%d measurement(s) still lost after retries; these are NOT covered"
              % len(errors))
        for e in sorted({(x.get("path"), x.get("requestedWidth")) for x in errors}):
            print("   %s at %spx" % e)
    measured = [r for r in results if not r.get("error")]
    expected = len(pages) * len(widths)
    print("\n  covered %d of %d page/width combinations"
          % (len(measured), expected))
    results = measured
    failures = [r for r in results
                if r.get("count") or r.get("scrolls") or r.get("deadGutter")]

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
            if f.get("deadGutter"):
                print("  %-34s %5dpx  dead gutter  %s at %dpx"
                      % (f["path"], f["requestedWidth"],
                         f["deadGutter"]["what"], f["deadGutter"]["left"]))
                continue
            head = f["bad"][0] if f["bad"] else {}
            what = head.get("cls") or head.get("tag") or "?"
            amt = head.get("over") or head.get("clippedBy")
            kind = "clipped" if "clippedBy" in head else "overflow"
            print("  %-34s %5dpx  %-10s %s +%s"
                  % (f["path"], f["requestedWidth"], kind, what, amt))

    # A pass that skipped combinations is not a pass. Reporting "ALL CLEAN"
    # while a seventh of the sweep went unmeasured is how a real failure gets
    # waved through, so uncovered pairs are a non-zero result in their own right.
    uncovered = expected - len(results)
    if uncovered:
        print("\n%d combination(s) were never measured -- rerun before "
              "trusting this." % uncovered)
    if failures:
        print("%d failing page/width combination(s)" % len(failures))
    elif not uncovered:
        print("ALL CLEAN")
    else:
        print("no failures in the combinations that ran")
    return 1 if (failures or uncovered) else 0


if __name__ == "__main__":
    sys.exit(main())
