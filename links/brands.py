"""Brand detection: does the URL name a product brand (not the marketplace)?

Rules, most reliable first:
  0. The link's product pages, read by `fetch_products`: the brand the page
     states (link_products.brand), else a listed brand at the start of the
     product's name. That is the shop's own statement, so it wins over the URL.
     A link whose products are too mixed to have one brand has no brand.
  1. Marketplace product pages have a known structure, so the brand is read
     from its position: Trendyol's first path segment, the start of a
     Hepsiburada slug.
  2. Other marketplace pages (store, campaign, search): the name at the end of
     the path, or the search text, counts when it starts with a listed brand.
  3. Any URL: a listed brand as a whole word in the path, the search or
     campaign parameter, or the site's own domain.
  4. Any other site that is not a marketplace, retailer, social network, news
     site, shortener, ... is taken to be a brand's own website: the brand is the
     site's name (og:site_name when the page was read, else the domain name).
     A shop on Shopier is the same thing on a shared domain, so its product
     links (shopier.com/{shop}/{product}) count the shop's name as the brand.
     A marketplace store that turned out to be a brand's own store counts too
     (`fetch_stores` reads the store page; `stores.store_brand` decides).

The brand list lives in brands.txt and the non-brand domains in
non_brand_domains.txt. The only entry point is
`detect_brand(url, site_name, product_brand, store_brand)`, so an AI-based
detector can replace it later without touching the resolver or API.
"""

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

BRANDS_FILE = Path(__file__).with_name("brands.txt")
DOMAINS_FILE = Path(__file__).with_name("non_brand_domains.txt")
MARKETPLACE = "marketplace"

# A real site name is short; a longer og:site_name is usually a slogan.
MAX_SITE_NAME = 40
# Country domains have a generic second part: in zara.com.tr the name is "zara", not "com".
GENERIC_SECOND_LEVEL = {"com", "co", "net", "org", "gov", "edu", "gen", "web", "av", "bel", "k12", "ac"}

# Query parameters that carry free text worth searching: the search box and the
# campaign name (Trendyol affiliate links: utm_campaign=elidor_<influencer>_<code>).
TEXT_PARAMS = ("q", "utm_campaign")

# A name starts with the brand, and a brand is at most this many words.
MAX_BRAND_WORDS = 3

_TRENDYOL_PRODUCT = re.compile(r"-p-\d+$")
_TRENDYOL_BRAND_PAGE = re.compile(r"^(.+)-x-b\d+$")
_HEPSIBURADA_PRODUCT = re.compile(r"^(.+)-pm?-[A-Za-z0-9]+$")
_LOOKS_LIKE_DOMAIN = re.compile(r"[\w-]+(\.[\w-]+)+")


def detect_brand(
    url: str, site_name: str = "", product_brand: str | None = None, store_brand: str | None = None
) -> str | None:
    """Return the brand the link points at, or None.

    `site_name` is the final page's og:site_name when the resolver could read
    it (see `page_site_name`); it is only used for a brand's own website.
    `product_brand` is what the link's product pages say (see
    `products.product_brands`): a brand name, "" when the products are too
    mixed to name one brand, or None when the pages say nothing about a brand.
    `store_brand` is the brand of the marketplace store the link points at,
    when the store is a brand's own (see `stores.store_brands`). It is the
    last resort: anything the URL itself names comes first.
    """
    # 0. The product pages say what the brand is: nothing in the URL beats that.
    if product_brand is not None:
        product_brand = " ".join(product_brand.split())
        return listed_spelling(product_brand) if product_brand else None

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    host_labels = host.split(".")
    segments = [segment for segment in unquote(parts.path).split("/") if segment]
    query = parse_qs(parts.query)
    category = site_category(host)  # None = an ordinary site
    is_marketplace = category == MARKETPLACE

    # 1. Product pages.
    if "trendyol" in host_labels or "trendyol-milla" in host_labels:
        brand = _from_trendyol(segments)
        if brand:
            return brand
    elif "hepsiburada" in host_labels and (slug := _hepsiburada_product_slug(segments)):
        # Final answer for a product page: a brand named later in the slug is
        # usually "compatible with X", not the product's brand.
        return _brand_at_start(_tokens(slug))

    # 2. Store, campaign and search pages: /magaza/estee-lauder, /ara?q=mavi+boxer.
    #    A store name alone is not a brand; it counts only when the list knows it.
    if is_marketplace:
        for name in [*segments[-1:], *query.get("q", [])]:
            brand = _brand_at_start(_tokens(name))
            if brand:
                return brand

    # 3. A listed brand anywhere, as a whole word.
    tokens = _tokens(" ".join(segments))
    for param in TEXT_PARAMS:
        tokens += _tokens(" ".join(query.get(param, [])))
    if category is None:
        tokens += _tokens(" ".join(host_labels))  # a listed brand's own site: oxxo.com.tr
    brand = _brand_anywhere(tokens)
    if brand:
        return brand

    # 4. An ordinary site names no listed brand: the site itself is the brand.
    if category is None:
        return _own_site_brand(host, site_name)
    #    A Shopier shop is one small brand's own storefront, not a marketplace of sellers.
    if "shopier" in host_labels:
        return _shopier_shop_brand(segments)
    #    A marketplace store that is the brand's own store.
    return store_brand or None


def brand_in_product_name(name: str) -> str | None:
    """A listed brand at the start of a product's name: "Dilvin Marka Beyaz Kazak" -> "Dilvin".

    Everyday-word brands (`^Mavi`) are not read from a product name: a shop's
    "Mavi Triko Kazak" is a blue sweater. They keep counting only in a
    marketplace URL, where the position says it is a brand.
    """
    return _brand_at_start(_tokens(name), everyday_words=False)


def brand_in_store_name(name: str) -> str | None:
    """A listed brand at the start of a marketplace store's name: "Koton" -> "Koton".

    Everyday-word brands count here: a store called "Mavi" is the brand.
    """
    return _brand_at_start(_tokens(name))


def name_starts_with(name: str, brand: str) -> bool:
    """"YVES SAINT LAURENT BEAUTY" starts with "Yves Saint Laurent": case, accents and punctuation are ignored."""
    words, brand_words = _tokens(name), _tokens(brand)
    return bool(brand_words) and words[: len(brand_words)] == brand_words


def listed_spelling(name: str) -> str:
    """The brand list's spelling of a name read from a page ("ESTEE LAUDER" -> "Estée Lauder"), else the name."""
    entry = _brand_index().get(tuple(_tokens(name)))
    return entry[0] if entry else name


def site_category(host: str) -> str | None:
    """Return the non-brand category of a host (marketplace, retailer, social, news, ...) or None."""
    suffixes, names, _shops = _domain_index()
    labels = host.split(".")
    for label in labels:
        if label in names:  # `amazon.*`: the name on any ending
            return names[label]
    for start in range(len(labels)):  # most specific first: play.google.com before google.com
        category = suffixes.get(".".join(labels[start:]))
        if category:
            return category
    return None


def page_site_name(chain: list[dict]) -> str:
    """The og:site_name the resolver stored on the last hop of a chain, or ""."""
    return chain[-1].get("site_name", "") if chain else ""


def marketplace_brand_id(chain: list[dict]) -> str:
    """Return the marketplace's brand id(s) from the tracking hop of a redirect chain.

    Trendyol short links pass through adj.st, whose query string describes the
    target page. Brand-filtered searches carry `BrandIds=670` (several ids are
    comma separated). Most links point at a seller store and have none.
    """
    for hop in chain:
        parts = urlsplit(hop.get("url", ""))
        if (parts.hostname or "").endswith(".adj.st"):
            ids = ",".join(value for value in parse_qs(parts.query).get("BrandIds", []) if value)
            if ids:
                return ids[:100]
    return ""


def _own_site_brand(host: str, site_name: str) -> str | None:
    """A brand's own website: what the page calls itself, else the name in the domain.

    www.akcabutik.com -> "Akcabutik", shop.mango.com -> "Mango", zara.com.tr -> "Zara".
    """
    site_name = " ".join(site_name.split())
    # Some pages put a slogan or their own address there; the domain rule reads better then.
    usable = site_name and len(site_name) <= MAX_SITE_NAME and not _LOOKS_LIKE_DOMAIN.fullmatch(site_name)
    if usable:
        return listed_spelling(site_name)

    labels = host.split(".")
    if len(labels) < 2 or labels[-1].isdigit():  # "localhost" or an IP address
        return None
    labels.pop()  # the ending: com, tr, shop
    if len(labels) > 1 and labels[-1] in GENERIC_SECOND_LEVEL:
        labels.pop()  # com.tr, co.uk
    return _display_name(labels[-1])


def _shopier_shop_brand(segments: list[str]) -> str | None:
    """shopier.com/{shop}/{product id}: the shop's name, unless the shop is listed as not a brand."""
    if len(segments) != 2 or not segments[1].isdigit():
        return None
    shop = segments[0].lower()
    if f"shopier.com/{shop}" in _domain_index()[2]:
        return None
    return _display_name(shop)


def _from_trendyol(segments: list[str]) -> str | None:
    """/{brand}/{product}-p-{id} and /{brand}-x-b{id}. Store and search pages carry only ids."""
    if len(segments) == 2 and _TRENDYOL_PRODUCT.search(segments[1]):
        return _display_name(segments[0])
    if len(segments) == 1 and (match := _TRENDYOL_BRAND_PAGE.match(segments[0])):
        return _display_name(match.group(1))
    return None


def _hepsiburada_product_slug(segments: list[str]) -> str | None:
    """/{slug}-p-{sku} or /{slug}-pm-{sku}: return the slug, or None for any other page."""
    if len(segments) == 1 and (match := _HEPSIBURADA_PRODUCT.match(segments[0])):
        return match.group(1)
    return None


def _brand_at_start(tokens: list[str], everyday_words: bool = True) -> str | None:
    """Whitelist rule: the first 1..MAX_BRAND_WORDS tokens must be a listed brand, longest first.

    `everyday_words=False` leaves out the brands marked `^` in brands.txt.
    """
    brands = _brand_index()
    for size in range(min(MAX_BRAND_WORDS, len(tokens)), 0, -1):
        entry = brands.get(tuple(tokens[:size]))
        if entry and (everyday_words or not entry[1]):
            return entry[0]
    return None


def _brand_anywhere(tokens: list[str]) -> str | None:
    """A listed brand as a run of whole tokens, longest brand first, then leftmost.

    Brands marked `^` in brands.txt are everyday words (Mavi = blue) and are
    skipped here: in the middle of a slug they are almost never the brand.
    """
    brands = _brand_index()
    for size in range(min(MAX_BRAND_WORDS + 1, len(tokens)), 0, -1):
        for start in range(len(tokens) - size + 1):
            entry = brands.get(tuple(tokens[start : start + size]))
            if entry and not entry[1]:
                return entry[0]
    return None


def _display_name(slug: str) -> str | None:
    """Prefer the list's spelling ("la-roche-posay" -> "La Roche-Posay"), else title-case the slug."""
    tokens = _tokens(slug)
    if not tokens or tokens == ["x"]:  # Trendyol's placeholder
        return None
    entry = _brand_index().get(tuple(tokens))
    return entry[0] if entry else " ".join(tokens).title()


def _tokens(text: str) -> list[str]:
    """Lowercase ASCII words: "Estée-Lauder/Çanta" -> ["estee", "lauder", "canta"].

    Matching whole tokens is the word-boundary rule: "mac" never matches "macbook".
    """
    text = text.lower().replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", text)


@lru_cache(maxsize=1)
def _brand_index() -> dict[tuple[str, ...], tuple[str, bool]]:
    """Load brands.txt once: {token tuple -> (display name, only_at_start)}.

    Line format: `Display Name`, `Display Name | alias, alias`, or `^Name` for a
    brand that is also an everyday word and only counts at the start of a name.
    """
    index: dict[tuple[str, ...], tuple[str, bool]] = {}
    for line in BRANDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        only_at_start = line.startswith("^")
        name, _, aliases = line.lstrip("^").partition("|")
        for spelling in [name, *aliases.split(",")]:
            tokens = tuple(_tokens(spelling))
            if tokens:
                index.setdefault(tokens, (name.strip(), only_at_start))
    return index


@lru_cache(maxsize=1)
def _domain_index() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Load non_brand_domains.txt once.

    Returns ({domain or ending -> category}, {name -> category}, {"shopier.com/shop" -> category}).
    """
    suffixes: dict[str, str] = {}
    names: dict[str, str] = {}
    shops: dict[str, str] = {}
    category = ""
    for line in DOMAINS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if not line:
            continue
        if line.startswith("["):
            category = line.strip("[]")
        elif "/" in line:
            shops[line.rstrip("/")] = category
        elif line.endswith(".*"):
            names[line[:-2]] = category
        else:
            suffixes[line] = category
    return suffixes, names, shops
