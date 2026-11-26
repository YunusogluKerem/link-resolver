import asyncio
import os
import time
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand, CommandError
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
# Address of an HTTP proxy (http://user:password@host:port). A secret: it lives in .env only.
PROXY_VARIABLE = "FETCH_PROXY_URL"


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
        parser.add_argument(
            "--cooldown", type=float, default=30.0, help="Seconds to wait after a site refuses a request (default 30)"
        )
        parser.add_argument("--timeout", type=float, default=15.0, help="Seconds per request (default 15)")
        parser.add_argument("--limit", type=int, help="Fetch at most N products (for a trial run)")
        parser.add_argument(
            "--per-link",
            type=int,
            help="Fetch at most N products of each link (3 are enough to decide a link's brand)",
        )
        parser.add_argument("--force", action="store_true", help="Fetch again products that already have a result")
        parser.add_argument(
            "--retry-failed", action="store_true", help="Also pick up products that were blocked or failed earlier"
        )
        parser.add_argument(
            "--proxy",
            action="store_true",
            help=f"Send the requests through the proxy in {PROXY_VARIABLE} (off by default)",
        )

    def handle(
        self, *args, platform, delay, cooldown, timeout, limit, per_link, force, retry_failed, proxy, **options
    ):
        # Opt-in per run: without --proxy the requests always leave from this machine.
        proxy_url = os.environ.get(PROXY_VARIABLE, "") if proxy else None
        if proxy and not proxy_url:
            raise CommandError(f"--proxy needs {PROXY_VARIABLE} in the environment (see .env.example).")

        targets = self._pending(platform or DEFAULT_PLATFORMS, force, retry_failed, limit, per_link)
        if not targets:
            self.stdout.write("Nothing to fetch. Use --retry-failed or --force to fetch products again.")
            return
        via = " through the proxy" if proxy_url else ""
        self.stdout.write(f"Fetching {len(targets)} products{via}, one request every {delay:g}s per site...")

        started = time.monotonic()
        try:
            counts = asyncio.run(self._run(targets, delay, cooldown, timeout, proxy_url))
        except KeyboardInterrupt:
            # Every product is saved as soon as it is read, so nothing fetched is lost.
            self.stdout.write("\nInterrupted. Run the same command again to continue with the products left.")
            return
        elapsed = time.monotonic() - started

        for (name, status), count in sorted(counts.items()):
            self.stdout.write(f"  {name:<13}{status:<10}{count:>6}")
        self.stdout.write(self.style.SUCCESS(f"Done in {elapsed:.1f}s"))

    def _pending(self, platforms, force, retry_failed, limit, per_link=None) -> list[tuple[str, str, str]]:
        """One (platform, product id, address) per product: shared by many links, fetched once.

        Order: every link's first product, then every link's second, and so on.
        A list link holds up to 100 products, and its brand is decided from a
        few of them, so a run that stops early has still covered every link.
        """
        wanted = [Status.PENDING, *(FAILED if retry_failed else [])]
        rows = LinkProducts.objects.filter(platform__in=platforms).select_related("link__detail").order_by("id")
        position_in_link: Counter = Counter()
        urls: dict[tuple[str, str], str] = {}
        rank: dict[tuple[str, str], int] = {}
        for row in rows.iterator(chunk_size=500):
            position = position_in_link[row.link_id]
            position_in_link[row.link_id] += 1
            if not force and row.status not in wanted:
                continue  # already read: it still takes its place in the link
            if per_link is not None and position >= per_link:
                continue
            key = (row.platform, row.product_id)
            is_page = row.source == LinkProducts.Source.PATH  # the link itself is the product page
            if key not in urls or is_page:
                url = product_url(row.platform, row.product_id, row.link.detail.link_detail if is_page else "")
                if url:
                    urls[key] = url
            if key in urls:
                rank[key] = min(rank.get(key, position), position)
        ordered = sorted(urls, key=rank.__getitem__)  # stable: ties keep the table's order
        return [(*key, urls[key]) for key in ordered][:limit]

    async def _run(self, targets, delay, cooldown, timeout, proxy_url=None) -> Counter:
        by_host: dict[str, list] = defaultdict(list)
        for target in targets:
            by_host[urlsplit(target[2]).hostname].append(target)
        counts: Counter = Counter()

        async with (
            build_client(timeout=timeout, proxy=proxy_url) as http2_client,
            build_client(timeout=timeout, proxy=proxy_url, http2=False) as http1_client,
        ):

            async def crawl(host: str, items: list) -> None:
                """One site: its products one after the other, with a pause between requests."""
                client = http2_client
                blocked_in_a_row = 0
                for index, (name, product_id, url) in enumerate(items):
                    if index:
                        # A refusal usually means "too fast": give the site a longer rest before going on.
                        await asyncio.sleep(cooldown if blocked_in_a_row else delay)
                    page = await fetch_product(client, url, product_id)
                    if page.refused_http2 and client is http2_client:
                        second = await fetch_product(http1_client, url, product_id)
                        if second.status != Status.BLOCKED:
                            # This site refuses HTTP/2 and answers HTTP/1.1: stay on it for the rest.
                            page, client = second, http1_client
                            self.stdout.write(f"  {host}: HTTP/2 refused, continuing over HTTP/1.1")
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
            product_name=page.name,
            brand=page.brand,
            status=page.status,
            http_version=page.http_version,
            fetched_at=timezone.now(),
        )
