from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from links.excel import parse_content_id, parse_links_cell, read_rows
from links.models import Links

BATCH_SIZE = 1000


class Command(BaseCommand):
    help = "Import (content_id, link) rows from an Excel file into the Links table."

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, help="Path to the .xlsx file")
        parser.add_argument("--sheet", help="Sheet name (default: the first sheet)")

    def handle(self, *args, path: Path, sheet: str | None, **options):
        if not path.is_file():
            raise CommandError(f"File not found: {path}")

        try:
            links, stats = self._collect(path, sheet)
        except (ValueError, KeyError) as exc:  # missing column / missing sheet
            raise CommandError(str(exc)) from exc

        # ignore_conflicts leans on the (content_id, link) unique constraint, so
        # importing the same file twice inserts nothing the second time.
        with transaction.atomic():
            before = Links.objects.count()
            Links.objects.bulk_create(links, batch_size=BATCH_SIZE, ignore_conflicts=True)
            inserted = Links.objects.count() - before

        self.stdout.write(
            f"Rows read:               {stats['rows']}\n"
            f"Skipped (empty/invalid): {stats['empty']}\n"
            f"Skipped (duplicate row): {stats['duplicate']}\n"
            f"Already in database:     {len(links) - inserted}"
        )
        self.stdout.write(self.style.SUCCESS(f"Imported:                {inserted}"))

    def _collect(self, path: Path, sheet: str | None) -> tuple[list[Links], dict[str, int]]:
        """Read the file into unsaved Links objects, skipping empty and repeated rows.

        A "duplicate" is the same content_id with the same link. The same link
        under a different content_id is kept: each content needs its own row.
        """
        stats = {"rows": 0, "empty": 0, "duplicate": 0}
        seen: set[tuple[int, str]] = set()
        links: list[Links] = []

        for raw_id, raw_links in read_rows(path, sheet):
            stats["rows"] += 1
            content_id = parse_content_id(raw_id)
            urls = parse_links_cell(raw_links)
            if content_id is None or not urls:
                stats["empty"] += 1
                continue
            for url in urls:
                if (content_id, url) in seen:
                    stats["duplicate"] += 1
                    continue
                seen.add((content_id, url))
                links.append(Links(content_id=content_id, link=url))

        return links, stats
