from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings


class Command(BaseCommand):
    help = "Enqueue a Celery task and optionally wait for the result."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=10, help="Seconds to wait for the result.")
        parser.add_argument("--no-wait", action="store_true", help="Only enqueue the task; do not wait for the result.")

    def handle(self, *args, **options):
        try:
            from celery.exceptions import TimeoutError as CeleryTimeoutError
        except Exception:  # pragma: no cover
            CeleryTimeoutError = TimeoutError

        from main.tasks import celery_ping

        try:
            async_result = celery_ping.delay()
        except Exception as e:
            raise CommandError(
                "Failed to enqueue task.\n"
                f"- broker: {settings.CELERY_BROKER_URL}\n"
                f"- backend: {settings.CELERY_RESULT_BACKEND}\n"
                "Start Redis (e.g. `docker compose up -d redis`) and a worker "
                "(`celery -A novel_creator worker -l info`).\n"
                f"Original error: {e}"
            )

        self.stdout.write(f"queued: {async_result.id}")

        if options["no_wait"]:
            return

        timeout = int(options["timeout"])
        try:
            result = async_result.get(timeout=timeout)
        except CeleryTimeoutError:
            raise CommandError(
                f"No result after {timeout}s. Is the worker running? "
                f"Try: celery -A novel_creator worker -l info"
            )
        except Exception as e:
            raise CommandError(f"Failed to read result. ({e})")

        self.stdout.write(f"result: {result}")
