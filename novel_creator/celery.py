import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "novel_creator.settings")

app = Celery("novel_creator")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

