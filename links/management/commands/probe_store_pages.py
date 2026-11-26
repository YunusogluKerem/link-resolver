import asyncio
import html
import re
from collections import Counter
from urllib.parse import urlsplit, urlunsplit

import httpx
from django.core.management.base import BaseCommand

from links.models import LinkDetails
from links.product_pages import read_start
from links.resolver import build_client, http2_refused

_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
# Names a store page may carry in its embedded data.
_SELLER_KEYS = re.compile(r'"(sellerName|merchantName|storeName|officialName)"\s*:\s*"([^"]{2,80})"')
# Product links on the page: /{brand}/{product}-p-{id}. The brand is the first segment.
_PRODUCT_LINK = re.compile(r'href="/([a-z0-9-]+)/[^"/]+-p-\d+')


class Command(BaseCommand):
    help = "Diagnostic: read a few Trendyol store pages and print what they give. Writes nothing."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10, help="Number of store pages to try (default 10)")
        parser.add_argument("--delay", type=float, default=2.0, help="Seconds between two pages (default 2)")
        parser.add_argument("--timeout", type=float, default=15.0, help="Seconds per request (default 15)")

    def handle(self, *args, limit, delay, timeout, **options):
        """Answers one question: can a store's name (and its products' brands) be read from its page?"""
        urls = self._store_urls(limit)
        if not urls:
            self.stdout.write("No Trendyol store link in the database.")
            return
        self.stdout.write(f"Reading {len(urls)} store pages, one every {delay:g}s. Nothing is saved.")
        asyncio.run(self._run(urls, delay, timeout))

    def _store_urls(self, limit: int) -> list[tuple[str, int]]:
        """The stores most links point at, without tracking parameters: [(url, link rows)]."""
        stores: Counter = Counter()
        final_urls = LinkDetails.objects.filter(link_detail__contains="trendyol.com/magaza/")
        for url in final_urls.values_list("link_detail", flat=True).iterator():
            parts = urlsplit(url)
            stores[urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))] += 1
        return stores.most_common(limit)

    async def _run(self, urls, delay, timeout) -> None:
        answered = 0
        async with (
            build_client(timeout=timeout) as http2_client,
            build_client(timeout=timeout, http2=False) as http1_client,
        ):
            for index, (url, rows) in enumerate(urls):
                if index:
                    await asyncio.sleep(delay)
                self.stdout.write(f"\n{index + 1}. {urlsplit(url).path}  ({rows} link rows)")
                try:
                    code, version, page = await self._read(http2_client, url)
                    self.stdout.write(f"   {version}: {code}")
                    if code == 403 and version == "HTTP/2":
                        code, version, page = await self._read(http1_client, url)
                        self.stdout.write(f"   {version}: {code}")
                except httpx.HTTPError as error:
                    self.stdout.write(f"   no answer: {type(error).__name__}")
                    continue
                if code == 200:
                    answered += 1
                    self._describe(page)
        self.stdout.write(f"\n{answered} of {len(urls)} store pages could be read.")

    async def _read(self, client: httpx.AsyncClient, url: str) -> tuple[int, str, str]:
        async with client.stream("GET", url, follow_redirects=True) as response:
            refused = http2_refused(response)
            page = "" if refused or response.status_code != 200 else await read_start(response)
            return response.status_code, response.http_version, page

    def _describe(self, page: str) -> None:
        title = _TITLE.search(page)
        heading = _H1.search(page)
        self.stdout.write(f"   size read: {len(page) // 1024} KB")
        self.stdout.write(f"   title: {_text(title.group(1)) if title else '-'}")
        self.stdout.write(f"   h1:    {_text(heading.group(1)) if heading else '-'}")
        names = Counter(f"{key}={value}" for key, value in _SELLER_KEYS.findall(page))
        self.stdout.write(f"   names in page data: {', '.join(name for name, _ in names.most_common(4)) or '-'}")
        brands = Counter(_PRODUCT_LINK.findall(page))
        listed = ", ".join(f"{brand} x{count}" for brand, count in brands.most_common(4))
        self.stdout.write(f"   product links: {sum(brands.values())}  brands in them: {listed or '-'}")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())[:120]
