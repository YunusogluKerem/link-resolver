"""Marketplace stores: which seller a link points at, and whether that store is a brand's own.

Most Trendyol links end at a store page (`/magaza/x-m-982060`) or at a product
list of one seller (`/sr?mid=982060&...`). The URL carries only the seller's
id; the store page says who the seller is. `fetch_stores` reads that page once
per seller and `store_brand` turns what it says into a brand, or into nothing.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import httpx

from links.brands import brand_in_store_name, listed_spelling, same_name
from links.models import LinkProducts, Stores
from links.resolver import BLOCKED_CODES, http2_refused

Status = LinkProducts.FetchStatus

_STORE_PATH = re.compile(r"^/magaza/.*-m-(\d+)$")
_DEEP_LINK_HOST = "adj.st"

# The seller block sits about 160 KB into the page, the product cards start
# between 400 KB and 1.3 MB. Reading stops as soon as enough products were seen.
MAX_STORE_BYTES = 2 * 1024 * 1024
ENOUGH_PRODUCTS = 20
_BRAND_KEY = b'"brandName":"'  # appears twice per product card
_SELLER_BLOCK = '"seller":{"id":'
_BRAND_NAME = re.compile(r'"brandName":("(?:[^"\\]|\\.)*")')
_VERIFIED_MARK = "verified-seller"  # the icon Trendyol shows next to an approved seller's name
GONE_CODES = {404, 410}
# A store counts as selling one brand when this share of the products shown carry it.
ONE_BRAND_SHARE = 0.5


@dataclass
class StorePage:
    status: str
    name: str = ""
    verified: bool = False
    product_brands: dict = field(default_factory=dict)
    http_version: str = ""
    refused_http2: bool = False


def seller_id(url: str, chain: list[dict] | None = None) -> str | None:
    """The Trendyol seller a link points at: /magaza/x-m-{id}, /sr?mid={id}, or MerchantId in the app deep link."""
    parts = urlsplit(url)
    host_labels = (parts.hostname or "").lower().split(".")
    if "trendyol" in host_labels:
        in_path = _STORE_PATH.match(parts.path)
        if in_path:
            return in_path.group(1)
        for value in parse_qs(parts.query).get("mid", []):
            if value.isdigit():
                return value
    for hop in chain or []:
        hop_parts = urlsplit(hop.get("url", ""))
        if (hop_parts.hostname or "").endswith(_DEEP_LINK_HOST):
            for value in parse_qs(hop_parts.query).get("MerchantId", []):
                if value.isdigit():
                    return value
    return None


def store_url(seller: str) -> str:
    """The store's address from its id alone; Trendyol's own links use the same placeholder name."""
    return f"https://www.trendyol.com/magaza/x-m-{seller}"


def store_brand(name: str, product_brands: dict) -> tuple[str | None, str]:
    """Is this store a brand's own? Returns (brand, why) or (None, "").

    A store name alone is not a brand: a seller called "OG Store" sells other
    people's products. It counts when one of these says so:
      listed    the name starts with a brand from brands.txt ("Koton");
      products  most products on its page carry one brand, and the store and
                that brand share a name ("VICTORIA'S SECRET" sells Victoria's
                Secret; "FRESH SCARFS" sells Freshscarfs).

    Trendyol's verified-seller mark is stored but not used: it is also given to
    authorised sellers of several brands, whose name is not a brand.
    """
    name = " ".join(name.split())
    if not name:
        return None, ""
    listed = brand_in_store_name(name)
    if listed:
        return listed, "listed"

    total = sum(product_brands.values())
    if total:
        brand, count = max(product_brands.items(), key=lambda item: item[1])
        if count >= total * ONE_BRAND_SHARE and same_name(name, brand):
            return listed_spelling(brand), "products"
    return None, ""


def store_brands() -> dict[str, str]:
    """seller id -> brand, for the stores that were read and are a brand's own."""
    result = {}
    for store in Stores.objects.filter(status=Status.OK):
        brand, _why = store_brand(store.name, store.product_brands)
        if brand:
            result[store.seller_id] = brand
    return result


async def fetch_store(client: httpx.AsyncClient, seller: str) -> StorePage:
    """Never raises: a refused, missing or unreadable page becomes a status."""
    try:
        async with client.stream("GET", store_url(seller), follow_redirects=True) as response:
            version = response.http_version
            if response.status_code in BLOCKED_CODES:
                return StorePage(Status.BLOCKED, http_version=version, refused_http2=http2_refused(response))
            # A closed store answers 404 or redirects away from its page.
            if response.status_code in GONE_CODES or f"-m-{seller}" not in response.url.path:
                return StorePage(Status.NOT_FOUND, http_version=version)
            if response.status_code != 200:
                return StorePage(Status.ERROR, http_version=version)
            page = await _read_store(response)
    except httpx.HTTPError:
        return StorePage(Status.ERROR)
    name, verified, product_brands = parse_store(page)
    return StorePage(Status.OK if name else Status.ERROR, name, verified, product_brands, version)


async def _read_store(response: httpx.Response) -> str:
    data = b""
    async for chunk in response.aiter_bytes():
        data += chunk
        if len(data) >= MAX_STORE_BYTES or data.count(_BRAND_KEY) >= 2 * ENOUGH_PRODUCTS:
            break
    return data[:MAX_STORE_BYTES].decode(response.encoding or "utf-8", errors="replace")


def parse_store(page: str) -> tuple[str, bool, dict]:
    """Return (store name, verified, {product brand: count}) from a store page; ("", False, {}) without a seller."""
    start = page.find(_SELLER_BLOCK)
    if start < 0:
        return "", False, {}
    try:
        seller, _end = json.JSONDecoder().raw_decode(page, start + len('"seller":'))
    except ValueError:
        return "", False, {}
    name = " ".join(str(seller.get("name") or "").split())[:200]
    marks = json.dumps(seller.get("markers") or [])
    brands: Counter = Counter()
    for raw in _BRAND_NAME.findall(page):
        try:
            brands[json.loads(raw)] += 1
        except ValueError:
            continue
    # Every card names its brand twice; the five most common are plenty to judge a store.
    product_brands = {brand: count // 2 or 1 for brand, count in brands.most_common(5) if brand}
    return name, _VERIFIED_MARK in marks, product_brands
