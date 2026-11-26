import asyncio
import time
from collections import Counter, defaultdict

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from links.brands import detect_brand, marketplace_brand_id, page_site_name
from links.models import LinkDetails, Links
from links.resolver import LinkResolver, ResolveResult, build_client

# Results are written every CHUNK_SIZE urls, so an interrupted run keeps its progress.
CHUNK_SIZE = 500
DETAIL_FIELDS = [
    "link_detail", "brand_exist", "brand_name", "marketplace_brand_id",
    "redirect_chain", "status", "status_code", "resolved_at",
]  # fmt: skip
# Timeouts and network errors are often temporary (a host slowing down under load).
FAILED = {LinkDetails.Status.TIMEOUT, LinkDetails.Status.ERROR}
# 429 means "too many requests right now": the same link usually works a few minutes later.
RATE_LIMITED = 429


class Command(BaseCommand):
    help = "Follow every link to its final URL and store the result in LinkDetails."

    def add_arguments(self, parser):
        parser.add_argument("--concurrency", type=int, default=50, help="Links in flight at once (default 50)")
        parser.add_argument("--per-host", type=int, default=8, help="Parallel requests per host (default 8)")
        parser.add_argument("--timeout", type=float, default=10.0, help="Seconds per request (default 10)")
        parser.add_argument("--limit", type=int, help="Resolve at most N unique urls (for a trial run)")
        parser.add_argument("--force", action="store_true", help="Resolve again links that already have a detail")
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Also pick up links that ended in timeout, error or 429 in an earlier run",
        )

    def handle(self, *args, concurrency, per_host, timeout, limit, force, retry_failed, **options):
        ids_by_url = self._pending(force, retry_failed, limit)
        link_rows = sum(len(ids) for ids in ids_by_url.values())
        if not ids_by_url:
            self.stdout.write("Nothing to resolve. Use --retry-failed or --force to resolve links again.")
            return
        self.stdout.write(f"Resolving {len(ids_by_url)} unique urls ({link_rows} link rows)...")

        started = time.monotonic()
        counts = asyncio.run(self._run(ids_by_url, concurrency, per_host, timeout))
        elapsed = time.monotonic() - started

        self.stdout.write(f"\nUnique urls: {len(ids_by_url)}   Link rows: {link_rows}")
        for status, _label in LinkDetails.Status.choices:
            self.stdout.write(f"  {status:<9}{counts[status]:>6}")
        self.stdout.write(self.style.SUCCESS(f"Done in {elapsed:.1f}s ({len(ids_by_url) / elapsed:.1f} urls/s)"))

    def _pending(self, force: bool, retry_failed: bool, limit: int | None) -> dict[str, list[int]]:
        """Group link ids by url: a url shared by many contents is requested once.

        Default: only links without a detail, so the command can be re-run safely.
        """
        queryset = Links.objects.order_by("id")
        if not force:
            todo = Q(detail__isnull=True)
            if retry_failed:
                todo |= Q(detail__status__in=FAILED) | Q(detail__status_code=RATE_LIMITED)
            queryset = queryset.filter(todo)
        ids_by_url: dict[str, list[int]] = defaultdict(list)
        for link_id, url in queryset.values_list("id", "link").iterator():
            if limit and url not in ids_by_url and len(ids_by_url) >= limit:
                continue
            ids_by_url[url].append(link_id)
        return ids_by_url

    async def _run(self, ids_by_url, concurrency, per_host, timeout) -> Counter:
        """Resolve every url, then give the temporary failures a single second chance."""
        latest: dict[str, ResolveResult] = {}
        slots = asyncio.Semaphore(concurrency)

        async with build_client(timeout=timeout, max_connections=concurrency * 2) as client:
            resolver = LinkResolver(client, per_host=per_host)

            async def resolve(url: str) -> ResolveResult:
                async with slots:
                    return await resolver.resolve(url)

            async def resolve_and_save(urls: list[str]) -> None:
                for start in range(0, len(urls), CHUNK_SIZE):
                    chunk = urls[start : start + CHUNK_SIZE]
                    results = await asyncio.gather(*(resolve(url) for url in chunk))
                    # The ORM is synchronous, so the write runs in a worker thread.
                    await sync_to_async(self._save)(dict(zip(chunk, results)), ids_by_url)
                    latest.update(zip(chunk, results))
                    self.stdout.write(f"  {start + len(chunk)}/{len(urls)} resolved")

            await resolve_and_save(list(ids_by_url))

            # By now the load is gone, so a host that was slow or rate limiting answers this time.
            failed = [url for url, result in latest.items() if _is_temporary(result)]
            if failed:
                self.stdout.write(f"Retrying {len(failed)} failed urls once...")
                await resolve_and_save(failed)
        return Counter(result.status for result in latest.values())

    def _save(self, results: dict[str, ResolveResult], ids_by_url: dict[str, list[int]]) -> None:
        """Upsert one LinkDetails row for every link that shares each resolved url."""
        now = timezone.now()
        details = []
        for url, result in results.items():
            # Look for the brand in the final URL; fall back to the raw link if unresolved.
            brand = detect_brand(result.final_url or url, page_site_name(result.chain))
            for link_id in ids_by_url[url]:
                details.append(
                    LinkDetails(
                        link_id=link_id,
                        link_detail=result.final_url,
                        brand_exist=brand is not None,
                        brand_name=brand,
                        marketplace_brand_id=marketplace_brand_id(result.chain),
                        redirect_chain=result.chain,
                        status=result.status,
                        status_code=result.status_code,
                        resolved_at=now,
                    )
                )
        # Insert, or update the existing row when re-running with --force.
        LinkDetails.objects.bulk_create(
            details, batch_size=500, update_conflicts=True, unique_fields=["link"], update_fields=DETAIL_FIELDS
        )


def _is_temporary(result: ResolveResult) -> bool:
    return result.status in FAILED or result.status_code == RATE_LIMITED
