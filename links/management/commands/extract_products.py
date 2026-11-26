from django.core.management.base import BaseCommand
from django.db.models import Count

from links.models import LinkDetails, LinkProducts
from links.products import extract_products

BATCH = 500


class Command(BaseCommand):
    help = "Fill the link_products table from the stored URLs and chains (no network requests)."

    def handle(self, *args, **options):
        """Re-runnable: adds what is missing, removes what the rules no longer find, keeps the rest."""
        wanted: dict[tuple[int, str, str], str] = {}
        for detail in LinkDetails.objects.select_related("link").iterator(chunk_size=BATCH):
            url = detail.link_detail or detail.link.link
            for ref in extract_products(url, detail.redirect_chain):
                wanted[(detail.link_id, ref.platform, ref.product_id)] = ref.source

        existing = {(row.link_id, row.platform, row.product_id): row for row in LinkProducts.objects.all()}
        new = [
            LinkProducts(link_id=key[0], platform=key[1], product_id=key[2], source=source)
            for key, source in wanted.items()
            if key not in existing
        ]
        LinkProducts.objects.bulk_create(new, batch_size=BATCH)

        moved = []  # same product, now found in a more direct place
        for key, row in existing.items():
            if key in wanted and row.source != wanted[key]:
                row.source = wanted[key]
                moved.append(row)
        LinkProducts.objects.bulk_update(moved, ["source"], batch_size=BATCH)

        stale = [row.pk for key, row in existing.items() if key not in wanted]
        for start in range(0, len(stale), BATCH):
            LinkProducts.objects.filter(pk__in=stale[start : start + BATCH]).delete()

        self.stdout.write(
            f"Added: {len(new)}   Source changed: {len(moved)}   Removed: {len(stale)}"
            f"   Total rows: {LinkProducts.objects.count()}"
        )
        self.stdout.write(f"{'platform':<14}{'source':<8}{'links':>7}{'rows':>7}{'products':>10}")
        summary = (
            LinkProducts.objects.values("platform", "source")
            .annotate(links=Count("link", distinct=True), rows=Count("id"), products=Count("product_id", distinct=True))
            .order_by("-rows")
        )
        for line in summary:
            self.stdout.write(
                f"{line['platform']:<14}{line['source']:<8}{line['links']:>7}{line['rows']:>7}{line['products']:>10}"
            )
