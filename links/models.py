from django.db import models


class Links(models.Model):
    """A raw link exactly as it was shared, tied to the content it came from.

    Model and field names follow the schema in the task document.
    """

    # `id` is the automatic primary key (BigAutoField).
    content_id = models.BigIntegerField(db_index=True, help_text="`id` column of the source Excel")
    link = models.CharField(max_length=2048, help_text="Raw link, cleaned of the `{...}` array wrapper")

    class Meta:
        db_table = "links"
        verbose_name = "link"
        verbose_name_plural = "links"
        ordering = ["id"]
        constraints = [
            # The same link is legitimately shared by many contents (695 rows in
            # the sample file), so only the exact pair is unique. This is also
            # what makes the import safe to run twice.
            models.UniqueConstraint(fields=["content_id", "link"], name="uniq_content_link"),
        ]

    def __str__(self) -> str:
        return f"{self.content_id}: {self.link[:80]}"


class LinkDetails(models.Model):
    """The outcome of resolving one link.

    The first three fields are the schema from the task document; the rest are
    additions, each justified in the README.
    """

    class Status(models.TextChoices):
        OK = "ok", "OK"  # final page answered normally
        RESOLVED = "resolved", "Resolved"  # redirects followed, final URL known, but that page refused us (403)
        BLOCKED = "blocked", "Blocked"  # refused on the very first request: no redirect seen at all
        TIMEOUT = "timeout", "Timeout"
        ERROR = "error", "Error"  # DNS failure, invalid URL, too many redirects, 404/5xx...

    # One detail row per link. A OneToOneField is a foreign key with a unique
    # constraint: the column is still `link_id` as in the schema, and re-running
    # the resolver updates the row instead of piling up duplicates.
    link = models.OneToOneField(Links, on_delete=models.CASCADE, related_name="detail")
    # Empty only when the very first request failed (blocked / timeout / error).
    link_detail = models.TextField(blank=True, help_text="Final URL after following all redirects")
    brand_exist = models.BooleanField(default=False)

    # --- additions to the schema ---
    brand_name = models.CharField(max_length=100, null=True, blank=True)
    # The marketplace's own numeric brand id(s), when the redirect chain carries
    # them (Trendyol: `BrandIds=670` or `617,101640`). Kept as text for a later
    # id -> name mapping; empty for most links.
    marketplace_brand_id = models.CharField(max_length=100, blank=True, default="")
    redirect_chain = models.JSONField(default=list, blank=True, help_text="Every hop: url + status code")
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True, help_text="HTTP status of the last hop")
    resolved_at = models.DateTimeField()

    class Meta:
        db_table = "link_details"
        verbose_name = "link detail"
        verbose_name_plural = "link details"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.link_id} -> {self.link_detail[:80]} [{self.status}]"


class LinkProducts(models.Model):
    """A product id found in a resolved link.

    A separate table because one link can carry many products: a Trendyol
    product list holds up to 100 ids. The ids are filled by `extract_products`
    from the stored URLs and chains, without any network request; the name and
    brand by `fetch_products`, which reads the product pages.
    """

    class Platform(models.TextChoices):
        TRENDYOL = "trendyol", "Trendyol"
        HEPSIBURADA = "hepsiburada", "Hepsiburada"
        AMAZON = "amazon", "Amazon"
        SHOPIER = "shopier", "Shopier"

    class Source(models.TextChoices):
        PATH = "path", "Product page URL"  # .../urun-p-123
        QUERY = "query", "id= parameter of the final URL"  # /sr?mid=..&id=1,2,3
        CHAIN = "chain", "App deep link in the redirect chain"  # ContentIds=1,2,3

    class FetchStatus(models.TextChoices):
        PENDING = "pending", "Not fetched yet"
        OK = "ok", "OK"
        NOT_FOUND = "not_found", "Not found"  # 404, or redirected away: the product was removed
        BLOCKED = "blocked", "Blocked"  # 401/403/429
        ERROR = "error", "Error"  # timeout, 5xx, or a page without a product name

    link = models.ForeignKey(Links, on_delete=models.CASCADE, related_name="products")
    platform = models.CharField(max_length=20, choices=Platform.choices)
    product_id = models.CharField(max_length=64, help_text="The platform's own id (Trendyol content id, Hepsiburada SKU, ASIN)")
    source = models.CharField(max_length=10, choices=Source.choices, help_text="Where in the link the id was found")

    # --- filled by `fetch_products` from the product's page ---
    # A product listed under many links is fetched once; all its rows get the same values.
    product_name = models.CharField(max_length=300, blank=True, default="")
    brand = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=10, choices=FetchStatus.choices, default=FetchStatus.PENDING, db_index=True)
    fetched_at = models.DateTimeField(null=True, blank=True)
    # Which protocol the page answered on ("HTTP/2" or "HTTP/1.1"): some sites refuse one of them.
    http_version = models.CharField(max_length=10, blank=True, default="")

    class Meta:
        db_table = "link_products"
        verbose_name = "link product"
        verbose_name_plural = "link products"
        ordering = ["id"]
        constraints = [
            # Makes `extract_products` safe to run twice.
            models.UniqueConstraint(fields=["link", "platform", "product_id"], name="uniq_link_product"),
        ]
        indexes = [
            # The same product appears under many links; look it up by its id.
            models.Index(fields=["platform", "product_id"], name="product_lookup"),
        ]

    def __str__(self) -> str:
        return f"{self.link_id}: {self.platform} {self.product_id}"


class Stores(models.Model):
    """A seller's store page on a marketplace, read once per seller.

    Most Trendyol links point at a store (/magaza/x-m-{seller id}) whose URL
    carries only the id. `fetch_stores` reads the store page and keeps what it
    says about the seller; `stores.store_brand` decides from that whether the
    store is a brand's own. Links are matched to a store by the seller id in
    their URL, so there is no foreign key: 341 links share the busiest store.
    """

    platform = models.CharField(max_length=20, default=LinkProducts.Platform.TRENDYOL)
    seller_id = models.CharField(max_length=32)
    link_count = models.PositiveIntegerField(default=0, help_text="Links pointing at this store; the busiest are read first")

    # --- read from the store page ---
    name = models.CharField(max_length=200, blank=True, default="")
    verified = models.BooleanField(default=False, help_text="The page carries the marketplace's verified-seller mark")
    product_brands = models.JSONField(default=dict, blank=True, help_text="Brands of the products shown on the page, with counts")
    status = models.CharField(
        max_length=10, choices=LinkProducts.FetchStatus.choices, default=LinkProducts.FetchStatus.PENDING, db_index=True
    )
    fetched_at = models.DateTimeField(null=True, blank=True)
    http_version = models.CharField(max_length=10, blank=True, default="")

    class Meta:
        db_table = "stores"
        verbose_name = "store"
        verbose_name_plural = "stores"
        ordering = ["-link_count", "id"]
        constraints = [models.UniqueConstraint(fields=["platform", "seller_id"], name="uniq_store")]

    def __str__(self) -> str:
        return f"{self.platform} {self.seller_id}: {self.name or '?'}"
