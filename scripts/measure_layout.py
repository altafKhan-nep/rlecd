"""Measure the admin in a real browser: what is wider than the viewport?

Whether a page scrolls sideways is a computed-layout fact, not something the
stylesheet appears to say. This drives headless Brave over CDP, logs in, and
walks the DOM looking for elements whose right edge passes the viewport -- the
things that produce a horizontal scrollbar on the page.

Usage:
    python measure.py <path> [width] [height]
e.g. python measure.py /admin/ 2000 1300
"""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

CHROME = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
BASE = "http://127.0.0.1:8000"
USER, PASSWORD = "admin", "Studio!Local2026"

PROBE = r"""
(() => {
  const vw = document.documentElement.clientWidth;
  const offenders = [];
  document.querySelectorAll('body *').forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1) return;
    if (r.right <= vw + 1) return;
    const cs = getComputedStyle(el);
    // An element that scrolls its own content is not a page-level offender.
    if (cs.overflowX === 'auto' || cs.overflowX === 'scroll' ||
        cs.overflowX === 'hidden') return;
    offenders.push({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className || '').slice(0, 54),
      id: el.id || '',
      w: Math.round(r.width),
      right: Math.round(r.right),
      over: Math.round(r.right - vw),
      position: cs.position,
      display: cs.display,
    });
  });
  offenders.sort((a, b) => b.over - a.over);
  const seen = new Set();
  const top = offenders.filter((o) => {
    const k = o.cls + '|' + o.tag;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(0, 16);
  return {
    viewport: vw,
    scrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    scrollsSideways: document.documentElement.scrollWidth > vw + 1,
    sidebar: (() => {
      const s = document.querySelector('.studio-sidebar');
      const c = document.getElementById('container');
      const m = c ? getComputedStyle(c) : null;
      return {
        sidebarWidth: s ? Math.round(s.getBoundingClientRect().width) : null,
        sidebarPosition: s ? getComputedStyle(s).position : null,
        containerMarginLeft: m ? m.marginLeft : null,
        containerLeft: c ? Math.round(c.getBoundingClientRect().left) : null,
        sidebarVar: getComputedStyle(document.documentElement)
          .getPropertyValue('--sidebar-w').trim(),
      };
    })(),
    offenders: top,
    offenderCount: offenders.length,
  };
})()
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CDP:
    def __init__(self, ws_url):
        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=25)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
             "Upgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
             ).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        self.i = 0

    def _send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            head = b"\x81" + bytes([0x80 | n])
        elif n < 65536:
            head = b"\x81" + bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head = b"\x81" + bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv(self):
        head = self.sock.recv(2)
        if len(head) < 2:
            return None
        n = head[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", self.sock.recv(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self.sock.recv(8))[0]
        data = b""
        while len(data) < n:
            data += self.sock.recv(n - len(data))
        return json.loads(data)

    def cmd(self, method, params=None):
        self.i += 1
        self._send({"id": self.i, "method": method, "params": params or {}})
        while True:
            msg = self._recv()
            if msg and msg.get("id") == self.i:
                return msg


def run(path, width=2000, height=1300, widths=None):
    """Log in once, then measure the path at every width in `widths`.

    Reusing one browser for the whole sweep matters: launching a fresh
    Chromium per width took eight seconds a point, which is long enough to
    tempt you into checking fewer widths than you meant to.
    """
    port = free_port()
    profile = tempfile.mkdtemp(prefix="brave-measure-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={profile}", f"--window-size={max(widths or [width])},{height}",
         "--no-first-run", "--no-default-browser-check", "--disable-gpu",
         "--disable-dev-shm-usage", f"{BASE}/admin/login/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws = None
        for _ in range(90):
            try:
                for tab in json.loads(urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json", timeout=1).read()):
                    if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                        ws = tab["webSocketDebuggerUrl"]
                        break
                if ws:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if not ws:
            return {"error": "devtools did not come up"}

        cdp = CDP(ws)
        cdp.cmd("Page.enable")
        cdp.cmd("Runtime.enable")
        cdp.cmd("Network.enable")
        time.sleep(1.2)

        # Log in through the real form, so the session cookie is genuine.
        cdp.cmd("Runtime.evaluate", {"expression": f"""
            (() => {{
              const f = document.querySelector('form');
              f.username.value = {json.dumps(USER)};
              f.password.value = {json.dumps(PASSWORD)};
              f.submit();
              return true;
            }})()
        """, "returnByValue": True})
        time.sleep(2.0)

        results = []
        for w in (widths or [width]):
            # A headless window cannot go below roughly 500px, which is far too
            # wide to trust as a phone test. Overriding device metrics is what
            # actually exercises a 390px viewport.
            cdp.cmd("Emulation.setDeviceMetricsOverride", {
                "width": w, "height": height,
                "deviceScaleFactor": 1, "mobile": w < 900,
            })
            cdp.cmd("Page.navigate", {"url": BASE + path})
            time.sleep(1.8)
            res = cdp.cmd("Runtime.evaluate",
                          {"expression": PROBE, "returnByValue": True})
            value = res.get("result", {}).get("result", {}).get("value")
            value = value or {"error": "probe returned nothing",
                              "raw": json.dumps(res)[:200]}
            value["requestedWidth"] = w
            results.append(value)
        return results
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def run_all(pages, widths, height=900):
    """Sweep every page at every width from a single logged-in session.

    Returns one probe result per (page, width) pair, each tagged with both.
    """
    port = free_port()
    profile = tempfile.mkdtemp(prefix="brave-sweep-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={profile}", f"--window-size={max(widths)},{height}",
         "--no-first-run", "--no-default-browser-check", "--disable-gpu",
         "--disable-dev-shm-usage", f"{BASE}/admin/login/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws = None
        for _ in range(90):
            try:
                for tab in json.loads(urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json", timeout=1).read()):
                    if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                        ws = tab["webSocketDebuggerUrl"]
                        break
                if ws:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if not ws:
            return [{"error": "devtools did not come up"}]

        cdp = CDP(ws)
        cdp.cmd("Page.enable")
        cdp.cmd("Runtime.enable")
        cdp.cmd("Network.enable")
        time.sleep(1.2)
        cdp.cmd("Runtime.evaluate", {"expression": f"""
            (() => {{
              const f = document.querySelector('form');
              if (!f) return false;
              f.username.value = {json.dumps(USER)};
              f.password.value = {json.dumps(PASSWORD)};
              f.submit();
              return true;
            }})()
        """, "returnByValue": True})
        time.sleep(2.0)

        out = []
        for path in pages:
            for w in widths:
                cdp.cmd("Emulation.setDeviceMetricsOverride", {
                    "width": w, "height": height,
                    "deviceScaleFactor": 1, "mobile": w < 900,
                })
                cdp.cmd("Page.navigate", {"url": BASE + path})
                time.sleep(0.7)
                try:
                    res = cdp.cmd("Runtime.evaluate",
                                  {"expression": PROBE, "returnByValue": True})
                    value = res.get("result", {}).get("result", {}).get("value")
                except (ConnectionResetError, OSError) as exc:
                    # A long session occasionally loses the socket. Report what
                    # was covered rather than failing the whole sweep; the
                    # caller batches pages so a dropped session costs one batch.
                    out.append({"path": path, "requestedWidth": w,
                                "count": 0, "scrolls": False,
                                "error": "session lost: %s" % exc})
                    return out
                if not isinstance(value, dict):
                    value = {"error": "probe returned nothing",
                             "raw": json.dumps(res)[:200]}
                value["requestedWidth"] = w
                value.setdefault("path", path)
                out.append(value)
        return out
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


WIDTHS = [1920, 1600, 1500, 1441, 1440, 1366, 1280, 1152, 1024, 900, 768, 640, 480, 390, 360]

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "/admin/"
    if len(sys.argv) > 2 and sys.argv[2] == "--sweep":
        rows = run(target, widths=WIDTHS)
        worst = 0
        for r in rows:
            off = (r.get("offenders") or [{}])[0]
            over = off.get("over") or 0
            bad = r.get("scrollsSideways") or over
            worst = max(worst, over or 0)
            print(f"  {'FAIL' if bad else ' ok '} {r.get('requestedWidth'):>5}px "
                  f"viewport={r.get('viewport'):>5} scrollW={r.get('scrollWidth'):>5}"
                  + (f"  clipped:{(off.get('cls') or off.get('tag'))} +{over}"
                     if over else ""))
        print(f"  => {'CLEAN' if worst == 0 else f'WORST OVERFLOW {worst}px'}")
    else:
        w = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
        h = int(sys.argv[3]) if len(sys.argv) > 3 else 1300
        print(json.dumps(run(target, w, h), indent=1))
