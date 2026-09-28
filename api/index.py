"""WSGI entrypoint for the Vercel Python runtime.

Vercel's @vercel/python runtime imports a module from the `api/` directory and
looks for a WSGI callable named `app`. Django exposes `application`, so it is
re-exported here under the name the runtime expects.

Nothing else belongs in this file: the runtime imports it on every cold start,
so any work done at module scope is paid on each new instance.
"""

from home_improvement.wsgi import application

# The runtime's documented contract.
app = application
