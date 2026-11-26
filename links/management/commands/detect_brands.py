from django.core.management.base import BaseCommand

from links.brands import detect_brand, marketplace_brand_id, page_site_name
from links.models import LinkDetails
from links.products import product_brands
from links.stores import seller_id, store_brands


class Command(BaseCommand):
    help = "Recompute the brand fields from the stored URLs, chains, product and store pages (no network requests)."

    def handle(self, *args, **options):
        """Lets the brand list or the detection method change without resolving links again."""
        details = list(LinkDetails.objects.select_related("link"))
        from_product_page = product_brands()
        from_store_page = store_brands()
        for detail in details:
            url = detail.link_detail or detail.link.link
            brand = detect_brand(
                url,
                page_site_name(detail.redirect_chain),
                from_product_page.get(detail.link_id),
                from_store_page.get(seller_id(url, detail.redirect_chain)),
            )
            detail.brand_exist = brand is not None
            detail.brand_name = brand
            detail.marketplace_brand_id = marketplace_brand_id(detail.redirect_chain)
        LinkDetails.objects.bulk_update(
            details, ["brand_exist", "brand_name", "marketplace_brand_id"], batch_size=500
        )

        with_brand = sum(detail.brand_exist for detail in details)
        self.stdout.write(self.style.SUCCESS(f"{with_brand} of {len(details)} link details have a brand."))
