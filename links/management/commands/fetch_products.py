import asyncio
import time
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from django.utils import timezone

from links.models import LinkProducts
from links.product_pages import FETCHABLE, ProductPage, fetch_product, product_url
from links.resolver import build_client

Platform = LinkProducts.Platform
Status = LinkProducts.FetchStatus

# Tried by default. Trendyol is only fetched when asked for with --platform.
DEFAULT_PLATFORMS = [Platform.HEPSIBURADA, Platform.SHOPIER]
FAILED = [Status.BLOCKED, Status.ERROR]
# A site that keeps refusing us is left alone for the rest of the run.
MAX_BLOCKED_IN_A_ROW = 5


class Command(BaseCommand):
    help = "Read the name and brand of the products in link_products from their pages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--platform",
            action="append",
            choices=FETCHABLE,
            help="Platform to fetch; repeat for several (default: hepsiburada and shopier)",
        )
        parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests to one site (default 1)")
        parser.add_argument("--timeout", type=float, default=15.0, help="Seconds per request (default 15)")
        parser.add_argument("--limit", type=int, help="Fetch at most N products (for a trial run)")
        parser.add_argument("--force", action="store_true", help="Fetch again products that already have a result")
        parser.add_argument(
            "--retry-failed", action="store_true", help="Also pick up products that were blocked or failed earlier"
        )

    def handle(self, *args, platform, delay, timeout, limit, force, retry_failed, **options):
        targets = self._pending(platform or DEFAULT_PLATFORMS, force, retry_failed, limit)
        if not targets:
            self.stdout.write("Nothing to fetch. Use --retry-failed or --force to fetch products again.")
            return
        self.stdout.write(f"Fetching {len(targets)} products, one request every {delay:g}s per site...")

        started = time.monotonic()
        try:
            counts = asyncio.run(self._run(targets, delay, timeout))
        except KeyboardInterrupt:
            # Every product is saved as soon as it is read, so nothing fetched is lost.
            self.stdout.write("\nInterrupted. Run the same command again to continue with the products left.")
            return
        elapsed = time.monotonic() - started

        for (name, status), count in sorted(counts.items()):
            self.stdout.write(f"  {name:<13}{status:<10}{count:>6}")
        self.stdout.write(self.style.SUCCESS(f"Done in {elapsed:.1f}s"))

    def _pending(self, platforms, force, retry_failed, limit) -> list[tuple[str, str, str]]:
        """One (platform, product id, address) per product: shared by many links, fetched once."""
        rows = LinkProducts.objects.filter(platform__in=platforms).select_related("link__detail").order_by("id")
        if not force:
            rows = rows.filter(status__in=[Status.PENDING, *(FAILED if retry_failed else [])])
        urls: dict[tuple[str, str], str] = {}
        for row in rows.iterator(chunk_size=500):
            key = (row.platform, row.product_id)
            is_page = row.source == LinkProducts.Source.PATH  # the link itself is the product page
            if key in urls and not is_page:
                continue
            url = product_url(row.platform, row.product_id, row.link.detail.link_detail if is_page else "")
            if url:
                urls[key] = url
        return [(*key, url) for key, url in urls.items()][:limit]

    async def _run(self, targets, delay, timeout) -> Counter:
        by_host: dict[str, list] = defaultdict(list)
        for target in targets:
            by_host[urlsplit(target[2]).hostname].append(target)
        counts: Counter = Counter()

        async with build_client(timeout=timeout) as client:

            async def crawl(host: str, items: list) -> None:
                """One site: its products one after the other, with a pause between requests."""
                blocked_in_a_row = 0
                for index, (name, product_id, url) in enumerate(items):
                    if index:
                        await asyncio.sleep(delay)
                    page = await fetch_product(client, url, product_id)
                    # The ORM is synchronous, so the write runs in a worker thread.
                    await sync_to_async(self._save)(name, product_id, page)
                    counts[(name, page.status)] += 1
                    blocked_in_a_row = blocked_in_a_row + 1 if page.status == Status.BLOCKED else 0
                    if blocked_in_a_row >= MAX_BLOCKED_IN_A_ROW:
                        left = len(items) - index - 1
                        self.stdout.write(f"  {host}: refused {blocked_in_a_row} times in a row, {left} left untouched")
                        return
                    if (index + 1) % 50 == 0:
                        self.stdout.write(f"  {host}: {index + 1}/{len(items)}")

            # Sites do not wait for each other; the pause is per site.
            await asyncio.gather(*(crawl(host, items) for host, items in by_host.items()))
        return counts

    def _save(self, name: str, product_id: str, page: ProductPage) -> None:
        """Write the result to every row of this product (the same product under many links)."""
        LinkProducts.objects.filter(platform=name, product_id=product_id).update(
            product_name=page.name, brand=page.brand, status=page.status, fetched_at=timezone.now()
        )
