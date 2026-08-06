"""WSGI 入口，供 Gunicorn 使用。"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pinche.settings")
application = get_wsgi_application()
