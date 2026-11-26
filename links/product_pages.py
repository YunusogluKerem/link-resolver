"""Reading a product's name and brand from its page. Used by `fetch_products`.

The name comes from the page's JSON-LD (`"@type": "Product"`, the structured
data shops publish for search engines) when it has one, else from og:title,
else from <title>. Only JSON-LD gives a brand.
"""

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from links.models import LinkProducts
from links.resolver import BLOCKED_CODES, http2_refused

Platform = LinkProducts.Platform
Status = LinkProducts.FetchStatus

# Amazon is left out: it answers a plain request with a bot-check page (200, no product).
FETCHABLE = (Platform.HEPSIBURADA, Platform.SHOPIER, Platform.TRENDYOL)

# Hepsiburada's JSON-LD sits about 180 KB into an 800 KB page.
MAX_PAGE_BYTES = 512 * 1024
GONE_CODES = {404, 410}
MAX_NAME, MAX_BRAND = 300, 100


@dataclass
class ProductPage:
    status: str
    name: str = ""
    brand: str = ""
    http_version: str = ""  # of the answer: "HTTP/2" or "HTTP/1.1"; "" when there was none
    refused_http2: bool = False  # worth one more try over HTTP/1.1 (see resolver.http2_refused)


def product_url(platform: str, product_id: str, page_url: str = "") -> str | None:
    """The address to read a product from, or None when the id alone is not enough.

    `page_url` is the link's final URL when that URL is the product page itself.
    """
    if page_url:
        parts = urlsplit(page_url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))  # drop tracking parameters
    if platform == Platform.TRENDYOL:
        # A product list gives only ids, so the address is built with a placeholder slug.
        return f"https://www.trendyol.com/x/x-p-{product_id}"
    return None


async def fetch_product(client: httpx.AsyncClient, url: str, product_id: str) -> ProductPage:
    """Never raises: a refused, missing or unreadable page becomes a status."""
    try:
        async with client.stream("GET", url, follow_redirects=True) as response:
            version = response.http_version
            if response.status_code in BLOCKED_CODES:
                return ProductPage(Status.BLOCKED, http_version=version, refused_http2=http2_refused(response))
            # A removed product answers 404, or redirects to its category page.
            if response.status_code in GONE_CODES or product_id not in str(response.url):
                return ProductPage(Status.NOT_FOUND, http_version=version)
            if response.status_code != 200:
                return ProductPage(Status.ERROR, http_version=version)
            page = await _read_start(response)
    except httpx.HTTPError:
        return ProductPage(Status.ERROR)
    name, brand = parse_product(page)
    return ProductPage(Status.OK if name else Status.ERROR, name, brand, version)


async def _read_start(response: httpx.Response) -> str:
    data = b""
    async for chunk in response.aiter_bytes():
        data += chunk
        if len(data) >= MAX_PAGE_BYTES:
            break
    return data[:MAX_PAGE_BYTES].decode(response.encoding or "utf-8", errors="replace")


# --- parsing ----------------------------------------------------------------

_JSON_LD = re.compile(r"<script\b[^>]*application/ld\+json[^>]*>(.*?)</script>", re.I | re.S)
_OG_TITLE = re.compile(r"<meta\b[^>]*\b(?:property|name)\s*=\s*[\"']og:title[\"'][^>]*>", re.I)
_CONTENT_ATTR = re.compile(r"\bcontent\s*=\s*([\"'])(.*?)\1", re.I | re.S)
_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
# What shops add after the product's name in a page title:
# "... - Fiyatı", "... Fiyatı, Yorumları", "... Fiyatı ve Yorumları | Trendyol".
_TITLE_TAIL = re.compile(
    r"\s*[-–|]?\s*Fiyat(?:ı|ları)(?:\s*(?:,|ve)\s*Yorumları)?\s*(?:[-–|]\s*[\w.]+\s*)?$", re.I
)


def parse_product(page: str) -> tuple[str, str]:
    """Return (name, brand) from a product page; ("", "") when it shows no product."""
    for block in _JSON_LD.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue  # sites do publish broken blocks; another one may be fine
        product = _find_product(data)
        if product and product.get("name"):
            brand = product.get("brand")
            if isinstance(brand, list):
                brand = brand[0] if brand else ""
            if isinstance(brand, dict):
                brand = brand.get("name")
            return _clean(product["name"], MAX_NAME), _clean(brand or "", MAX_BRAND)

    meta = _OG_TITLE.search(page)
    content = _CONTENT_ATTR.search(meta.group(0)) if meta else None
    if content and _clean(content.group(2), MAX_NAME):
        return _clean(content.group(2), MAX_NAME), ""
    title = _TITLE.search(page)
    if not title:
        return "", ""
    name = _clean(title.group(1), MAX_NAME)
    return _clean(_TITLE_TAIL.sub("", name), MAX_NAME), ""


def _find_product(data, depth: int = 0) -> dict | None:
    """The first `"@type": "Product"` object, wherever it is nested (`@graph`, `mainEntity`, a list)."""
    if depth > 6:
        return None
    if isinstance(data, dict):
        kinds = data.get("@type")
        if "Product" in (kinds if isinstance(kinds, list) else [kinds]):
            return data
        data = list(data.values())
    if isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                found = _find_product(item, depth + 1)
                if found:
                    return found
    return None


def _clean(text, limit: int) -> str:
    """One line, entities decoded, no dangling separator ("Robot Süpürge -": the colour is a separate field)."""
    text = " ".join(html.unescape(str(text)).split())
    return text[:limit].strip(" -|")
