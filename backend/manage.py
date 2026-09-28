#!/usr/bin/env python
"""Django's command-line utility for administrative tasks.

Run from anywhere:

    .venv/bin/python backend/manage.py test
    .venv/bin/python backend/manage.py capture_content
"""
import os
import sys
from pathlib import Path

# This file lives in backend/, alongside the apps, so that directory is the
# import root. Keeping the apps importable as top-level `main`, `crm` and
# `content` is deliberate: those names are the app *labels* recorded in
# migrations and referenced by the stored page markup (`{% extends
# 'main/x.html' %}`), so re-rooting them would break both.
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'home_improvement.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
