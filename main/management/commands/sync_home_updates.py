from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from main.models import HomeUpdate


class Command(BaseCommand):
    help = "Sync home page memo-board updates from a JSON file into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default="",
            help="Path to JSON file. Defaults to <BASE_DIR>/main/data/home_updates.json.",
        )
        parser.add_argument(
            "--prune-missing",
            action="store_true",
            help="Delete DB rows with non-empty source_key missing from JSON.",
        )

    def handle(self, *args, **options):
        default_path = Path(settings.BASE_DIR) / "main" / "data" / "home_updates.json"
        raw_path = (options.get("path") or "").strip()
        file_path = Path(raw_path) if raw_path else default_path

        if not file_path.exists():
            raise CommandError(f"JSON file not found: {file_path}")

        updates = self._load_updates(file_path)
        prune_missing = bool(options.get("prune_missing"))

        created = 0
        updated = 0
        source_keys = []

        with transaction.atomic():
            for item in updates:
                source_key = item["source_key"]
                source_keys.append(source_key)
                _obj, was_created = HomeUpdate.objects.update_or_create(
                    source_key=source_key,
                    defaults={
                        "date": item["date"],
                        "title": item["title"],
                        "body": item["body"],
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

            deleted = 0
            if prune_missing:
                deleted, _ = HomeUpdate.objects.exclude(source_key="").exclude(source_key__in=source_keys).delete()

        kept = len(updates)
        self.stdout.write(
            self.style.SUCCESS(
                "Synced home updates "
                f"from {file_path} (entries={kept}, created={created}, updated={updated}, deleted={deleted})."
            )
        )

    def _load_updates(self, file_path: Path) -> list[dict]:
        try:
            raw_data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {file_path}: {exc}") from exc

        if not isinstance(raw_data, list):
            raise CommandError("Home updates JSON must be a list of objects.")

        seen_keys = set()
        updates = []
        for index, row in enumerate(raw_data, start=1):
            if not isinstance(row, dict):
                raise CommandError(f"Entry #{index} must be an object.")

            source_key = str(row.get("source_key", "")).strip()
            title = str(row.get("title", "")).strip()
            body = str(row.get("body", "")).strip()
            date_raw = str(row.get("date", "")).strip()

            if not source_key:
                raise CommandError(f"Entry #{index} is missing required field 'source_key'.")
            if source_key in seen_keys:
                raise CommandError(f"Duplicate source_key in JSON: {source_key}")
            if not title:
                raise CommandError(f"Entry #{index} is missing required field 'title'.")
            if not date_raw:
                raise CommandError(f"Entry #{index} is missing required field 'date' (YYYY-MM-DD).")

            try:
                parsed_date = date.fromisoformat(date_raw)
            except ValueError as exc:
                raise CommandError(
                    f"Entry #{index} has invalid date '{date_raw}'. Use YYYY-MM-DD."
                ) from exc

            seen_keys.add(source_key)
            updates.append(
                {
                    "source_key": source_key,
                    "date": parsed_date,
                    "title": title,
                    "body": body,
                }
            )

        return updates
