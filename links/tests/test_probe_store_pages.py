from io import StringIO
from unittest import mock

import httpx
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from links.models import LinkDetails, LinkProducts, Links

STORE = "https://www.trendyol.com/magaza/ornek-m-123456"
PAGE = """<html><head><title>Örnek Mağaza Ürünleri - Trendyol</title></head><body>
<h1>Örnek <b>Mağaza</b></h1><script>{"sellerName":"Örnek Ticaret"}</script>
<a href="/tchibo/termos-p-1">a</a><a href="/tchibo/soyucu-p-2">b</a><a href="/dove/sabun-p-3">c</a></body></html>"""


class ProbeStorePagesTests(TestCase):
    def test_reads_store_pages_over_http1_when_http2_is_refused_and_saves_nothing(self):
        for content_id in (1, 2):  # the same store behind two links
            link = Links.objects.create(content_id=content_id, link=f"https://ty.gl/{content_id}")
            LinkDetails.objects.create(
                link=link, link_detail=f"{STORE}?adjust_t={content_id}", redirect_chain=[],
                status=LinkDetails.Status.RESOLVED, status_code=403, resolved_at=timezone.now(),
            )  # fmt: skip
        calls: list[str] = []

        def fake_client(http2: bool = True, **kwargs) -> httpx.AsyncClient:
            def handler(request: httpx.Request) -> httpx.Response:
                calls.append(f"{'HTTP/2' if http2 else 'HTTP/1.1'} {request.url}")
                if http2:
                    return httpx.Response(403, extensions={"http_version": b"HTTP/2"})
                return httpx.Response(200, html=PAGE)

            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        out = StringIO()
        with mock.patch("links.management.commands.probe_store_pages.build_client", fake_client):
            call_command("probe_store_pages", stdout=out)
        output = out.getvalue()

        self.assertEqual(calls, [f"HTTP/2 {STORE}", f"HTTP/1.1 {STORE}"])  # one store, asked once per protocol
        self.assertIn("(2 link rows)", output)
        self.assertIn("title: Örnek Mağaza Ürünleri - Trendyol", output)
        self.assertIn("h1:    Örnek Mağaza", output)
        self.assertIn("sellerName=Örnek Ticaret", output)
        self.assertIn("tchibo x2, dove x1", output)
        self.assertIn("1 of 1 store pages could be read", output)
        self.assertEqual(LinkDetails.objects.get(link__content_id=1).status, "resolved")
        self.assertEqual(LinkProducts.objects.count(), 0)
