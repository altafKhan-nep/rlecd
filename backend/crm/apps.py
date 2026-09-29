from django.apps import AppConfig


class CrmConfig(AppConfig):
    name = 'crm'
    verbose_name = 'CRM'

    def ready(self):
        # Connected here rather than at import time in audit.py, so the app
        # registry is populated and the watched models resolve.
        from crm import audit

        audit.connect()
