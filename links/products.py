"""Product ids carried by a resolved link. Pure text work: no network.

A link shows its products in one of three places:
  * the final URL is a product page and the id is in its path
    (Trendyol `...-p-123`, Hepsiburada `...-p-HBC...`, Amazon `/dp/B0...`, Shopier `/shop/123`);
  * the final URL is a Trendyol product list: `/sr?mid=..&id=1,2,3`;
  * the app deep link in the redirect chain (the adj.st hop) repeats them:
    `ContentIds=1,2,3`, `sku=HBC...`. Used when the final URL does not have them.

`extract_products(url, chain)` finds the ids; `product_brands()` hands the brands
read from the product pages to brand detection.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from links.models import LinkProducts

Platform = LinkProducts.Platform
Source = LinkProducts.Source

# Where the id sits in the path of a product page.
_PATH_PATTERNS = {
    Platform.TRENDYOL: re.compile(r"-p-(\d+)$"),
    # Hepsiburada's own SKUs are upper case (HBC...); a seller's can be lower case.
    Platform.HEPSIBURADA: re.compile(r"-pm?-([A-Za-z0-9]{8,})$"),
    Platform.AMAZON: re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)"),
    Platform.SHOPIER: re.compile(r"/(\d{6,})$"),
}
# Parameters of the app deep link that name products, and whose app it is.
_CHAIN_PARAMS = {
    "ContentIds": Platform.TRENDYOL,
    "ContentId": Platform.TRENDYOL,
    "sku": Platform.HEPSIBURADA,
    "productId": Platform.HEPSIBURADA,
}
_DEEP_LINK_HOST = "adj.st"
_VALID_ID = re.compile(r"[A-Za-z0-9]{1,64}")


@dataclass(frozen=True)
class ProductRef:
    platform: str
    product_id: str
    source: str


def extract_products(url: str, chain: list[dict] | None = None) -> list[ProductRef]:
    """Return every product id the link carries. The most direct source wins: path, query, chain."""
    found: dict[tuple[str, str], ProductRef] = {}

    def add(platform: str, text: str, source: str) -> None:
        for product_id in text.split(","):
            product_id = product_id.strip()
            if _VALID_ID.fullmatch(product_id):
                found.setdefault((platform, product_id), ProductRef(platform, product_id, source))

    parts = urlsplit(url)
    platform = _platform(parts.hostname or "")
    if platform:
        in_path = _PATH_PATTERNS[platform].search(parts.path)
        if in_path:
            add(platform, in_path.group(1), Source.PATH)
        if platform == Platform.TRENDYOL:
            for value in parse_qs(parts.query).get("id", []):
                add(platform, value, Source.QUERY)

    for hop in chain or []:
        hop_parts = urlsplit(hop.get("url", ""))
        # Only the deep link hop: on an ordinary site `sku=` means that site's own product.
        if not (hop_parts.hostname or "").endswith(_DEEP_LINK_HOST):
            continue
        query = parse_qs(hop_parts.query)
        for param, param_platform in _CHAIN_PARAMS.items():
            for value in query.get(param, []):
                add(param_platform, value, Source.CHAIN)

    return list(found.values())


def _platform(host: str) -> str | None:
    """trendyol.com, www.trendyol-milla.com, amazon.com.tr, link.amazon -> the platform's name."""
    for label in host.lower().split("."):
        for platform in _PATH_PATTERNS:
            if label == platform or label.startswith(f"{platform}-"):
                return platform
    return None


def product_brands() -> dict[int, str]:
    """link id -> the brand its product pages name, for links whose pages named any brand.

    A product list can hold several brands. A brand stands for the link only
    when at least half of the link's products that were read carry it; a more
    mixed link maps to "", which brand detection reads as "no brand".
    """
    read: Counter = Counter()  # products whose page was read, per link
    by_link: dict[int, Counter] = defaultdict(Counter)
    rows = LinkProducts.objects.filter(status=LinkProducts.FetchStatus.OK).order_by("id")
    for link_id, brand in rows.values_list("link_id", "brand"):
        read[link_id] += 1
        if brand:
            by_link[link_id][brand] += 1
    result = {}
    for link_id, brands in by_link.items():
        brand, count = brands.most_common(1)[0]
        result[link_id] = brand if count * 2 >= read[link_id] else ""
    return result
