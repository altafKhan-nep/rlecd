"""
WSGI config for home_improvement project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path

from django.core.wsgi import get_wsgi_application

# Put backend/ on sys.path before the settings import below.
#
# Every app in this project lives directly under backend/ and is imported as a
# top-level module: `main`, `crm`, `content`, `home_improvement`. Those names
# are the app labels recorded in the migrations and referenced by the stored
# page markup, so they must stay top-level rather than become
# `backend.home_improvement...`.
#
# manage.py inserts the same directory for the same reason. This file needs it
# too because a WSGI server does not get it for free: Vercel loads this file by
# absolute path from the repository root, so sys.path contains the repository
# root and not backend/, and the import below fails with
# "No module named 'home_improvement'" before Django ever starts.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'home_improvement.settings')

application = get_wsgi_application()
