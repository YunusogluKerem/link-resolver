import asyncio
import time
from collections import Counter

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from django.utils import timezone

from links.models import LinkDetails, LinkProducts, Stores
from links.resolver import build_client
from links.stores import StorePage, fetch_store, seller_id

Status = LinkProducts.FetchStatus
FAILED = [Status.BLOCKED, Status.ERROR]
# A site that keeps refusing us is left alone for the rest of the run.
MAX_BLOCKED_IN_A_ROW = 5


class Command(BaseCommand):
    help = "Read the store page of every seller the links point at (name, verified mark, product brands)."

    def add_arguments(self, parser):
        parser.add_argument("--delay", type=float, default=2.5, help="Seconds between two requests (default 2.5)")
        parser.add_argument(
            "--cooldown", type=float, default=30.0, help="Seconds to wait after the site refuses a request (default 30)"
        )
        parser.add_argument("--timeout", type=float, default=20.0, help="Seconds per request (default 20)")
        parser.add_argument("--limit", type=int, help="Read at most N stores (for a trial run)")
        parser.add_argument("--force", action="store_true", help="Read again stores that already have a result")
        parser.add_argument(
            "--retry-failed", action="store_true", help="Also pick up stores that were blocked or failed earlier"
        )

    def handle(self, *args, delay, cooldown, timeout, limit, force, retry_failed, **options):
        known, added = self._sync_sellers()
        self.stdout.write(f"Sellers in the links: {known} ({added} new)")

        stores = Stores.objects.all()  # busiest first: see Stores.Meta.ordering
        if not force:
            stores = stores.filter(status__in=[Status.PENDING, *(FAILED if retry_failed else [])])
        sellers = list(stores.values_list("seller_id", flat=True)[:limit])
        if not sellers:
            self.stdout.write("Nothing to read. Use --retry-failed or --force to read stores again.")
            return
        self.stdout.write(f"Reading {len(sellers)} store pages, one every {delay:g}s, busiest stores first...")

        started = time.monotonic()
        try:
            counts = asyncio.run(self._run(sellers, delay, cooldown, timeout))
        except KeyboardInterrupt:
            # Every store is saved as soon as it is read, so nothing fetched is lost.
            self.stdout.write("\nInterrupted. Run the same command again to continue with the stores left.")
            return
        for status, count in sorted(counts.items()):
            self.stdout.write(f"  {status:<10}{count:>6}")
        self.stdout.write(self.style.SUCCESS(f"Done in {time.monotonic() - started:.1f}s"))

    def _sync_sellers(self) -> tuple[int, int]:
        """Make one Stores row per seller found in the links (no network). Returns (sellers, new ones)."""
        link_counts: Counter = Counter()
        for detail in LinkDetails.objects.select_related("link").iterator(chunk_size=500):
            seller = seller_id(detail.link_detail or detail.link.link, detail.redirect_chain)
            if seller:
                link_counts[seller] += 1
        existing = {store.seller_id: store for store in Stores.objects.all()}
        new = [Stores(seller_id=seller, link_count=count) for seller, count in link_counts.items() if seller not in existing]
        Stores.objects.bulk_create(new, batch_size=500)
        changed = []
        for seller, store in existing.items():
            if store.link_count != link_counts.get(seller, 0):
                store.link_count = link_counts.get(seller, 0)
                changed.append(store)
        Stores.objects.bulk_update(changed, ["link_count"], batch_size=500)
        return len(link_counts), len(new)

    async def _run(self, sellers, delay, cooldown, timeout) -> Counter:
        counts: Counter = Counter()
        async with (
            build_client(timeout=timeout) as http2_client,
            build_client(timeout=timeout, http2=False) as http1_client,
        ):
            client = http2_client
            blocked_in_a_row = 0
            for index, seller in enumerate(sellers):
                if index:
                    # A refusal usually means "too fast": give the site a longer rest before going on.
                    await asyncio.sleep(cooldown if blocked_in_a_row else delay)
                page = await fetch_store(client, seller)
                if page.refused_http2 and client is http2_client:
                    second = await fetch_store(http1_client, seller)
                    if second.status != Status.BLOCKED:
                        # The site refuses HTTP/2 and answers HTTP/1.1: stay on it for the rest.
                        page, client = second, http1_client
                        self.stdout.write("  HTTP/2 refused, continuing over HTTP/1.1")
                # The ORM is synchronous, so the write runs in a worker thread.
                await sync_to_async(self._save)(seller, page)
                counts[page.status] += 1
                blocked_in_a_row = blocked_in_a_row + 1 if page.status == Status.BLOCKED else 0
                if blocked_in_a_row >= MAX_BLOCKED_IN_A_ROW:
                    left = len(sellers) - index - 1
                    self.stdout.write(f"  refused {blocked_in_a_row} times in a row, {left} stores left untouched")
                    break
                if (index + 1) % 50 == 0:
                    self.stdout.write(f"  {index + 1}/{len(sellers)}")
        return counts

    def _save(self, seller: str, page: StorePage) -> None:
        Stores.objects.filter(seller_id=seller).update(
            name=page.name,
            verified=page.verified,
            product_brands=page.product_brands,
            status=page.status,
            http_version=page.http_version,
            fetched_at=timezone.now(),
        )
