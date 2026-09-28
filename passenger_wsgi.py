import os
import sys

# Project path
project_home = "/home/hightech/homeimp.quantumcoresoftware.com/home_improvement"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Virtualenv path
venv_path = "/home/hightech/virtualenv/homeimp.quantumcoresoftware.com/home_improvement/3.10"

# Activate virtualenv
activate_this = os.path.join(venv_path, "bin", "activate_this.py")
with open(activate_this) as file_:
    exec(file_.read(), dict(__file__=activate_this))

# Django settings module
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "home_improvement.settings")

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()