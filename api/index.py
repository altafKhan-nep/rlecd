"""WSGI entrypoint for the Vercel Python runtime.

Vercel's @vercel/python runtime imports a module from the `api/` directory and
looks for a WSGI callable named `app`. Django exposes `application`, so it is
re-exported here under the name the runtime expects.

`backend/` is prepended to the import path because the Django apps live there
and must import as top-level `main`, `crm` and `content` -- the names recorded
in migrations and referenced by the stored page markup. The import is
deliberately done here rather than relying on the working directory, since the
runtime executes from the repository root.

Nothing else belongs in this file: the runtime imports it on every cold start,
so any work done at module scope is paid on each new instance.
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from home_improvement.wsgi import application  # noqa: E402

# The runtime's documented contract.
app = application
