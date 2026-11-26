from io import StringIO
from unittest import mock

import httpx
from django.core.management import call_command
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from links.models import LinkDetails, LinkProducts, Links
from links.product_pages import parse_product, product_url

HB_PAGE = "https://www.hepsiburada.com/roborock-s9-robot-supurge-p-HBCV0000EUMLOS"
JSON_LD = """
<html><head><title>Roborock S9 Fiyatı</title>
<script type="application/ld+json">{broken</script>
<script type="application/ld+json">
{"@type": "WebPage", "name": "Sayfa", "@graph": [
  {"@type": "Product", "name": "Roborock S9 Robot S&uuml;p&uuml;rge -", "brand": {"@type": "Brand", "name": "Roborock"}}
]}
</script></head></html>
"""


class ParseProductTests(SimpleTestCase):
    def test_json_ld_product_gives_name_and_brand(self):
        self.assertEqual(parse_product(JSON_LD), ("Roborock S9 Robot Süpürge", "Roborock"))

    def test_brand_can_be_plain_text(self):
        page = '<script type="application/ld+json">[{"@type": ["Product"], "name": "Kolye", "brand": "Atölye"}]</script>'
        self.assertEqual(parse_product(page), ("Kolye", "Atölye"))

    def test_without_json_ld_og_title_then_title_is_used(self):
        og = '<title>Mop Seti | Bir Mağaza</title><meta property="og:title" content="Mop Seti 1+1"/>'
        self.assertEqual(parse_product(og), ("Mop Seti 1+1", ""))
        self.assertEqual(parse_product("<title>\n Mop Seti | Bir Mağaza </title>"), ("Mop Seti | Bir Mağaza", ""))

    def test_page_without_a_name(self):
        self.assertEqual(parse_product("<html><body>bos</body></html>"), ("", ""))


class ProductUrlTests(SimpleTestCase):
    def test_product_page_link_is_used_without_tracking_parameters(self):
        self.assertEqual(product_url("hepsiburada", "HBCV0000EUMLOS", HB_PAGE + "?ftid=abc&magaza=x"), HB_PAGE)

    def test_trendyol_id_alone_builds_an_address(self):
        self.assertEqual(product_url("trendyol", "123"), "https://www.trendyol.com/x/x-p-123")

    def test_other_platforms_need_the_page_link(self):
        self.assertIsNone(product_url("hepsiburada", "HBCV0000EUMLOS"))


def make_link(content_id: int, final_url: str) -> Links:
    link = Links.objects.create(content_id=content_id, link=f"https://app.hb.biz/{content_id}")
    LinkDetails.objects.create(
        link=link, link_detail=final_url, redirect_chain=[], status=LinkDetails.Status.OK, resolved_at=timezone.now()
    )
    return link


class FetchProductsCommandTests(TransactionTestCase):
    """The whole command against a fake network. (TransactionTestCase: results are saved from a worker thread.)"""

    def _run(self, routes: dict, *args: str) -> tuple[str, list[str]]:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return routes[str(request.url)]

        def fake_client(**kwargs) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        out = StringIO()
        with (
            mock.patch("links.management.commands.fetch_products.build_client", fake_client),
            mock.patch("links.management.commands.fetch_products.asyncio.sleep", mock.AsyncMock()),  # skip the pauses
        ):
            call_command("fetch_products", *args, stdout=out)
        return out.getvalue(), calls

    def setUp(self):
        gone = "https://www.hepsiburada.com/eski-urun-p-HBV00000GONE1"
        make_link(1, HB_PAGE + "?ftid=abc")
        make_link(2, HB_PAGE + "?ftid=xyz")  # the same product under another content
        make_link(3, gone)
        make_link(4, "https://www.shopier.com/birmagaza/45116827")
        make_link(5, "https://www.trendyol.com/sr/?mid=1&id=111")
        call_command("extract_products", stdout=StringIO())
        self.routes = {
            HB_PAGE: httpx.Response(200, html=JSON_LD),
            # A removed product redirects to its category page.
            gone: httpx.Response(302, headers={"location": "https://www.hepsiburada.com/kategori-c-1"}),
            "https://www.hepsiburada.com/kategori-c-1": httpx.Response(200, html="<title>Kategori</title>"),
            "https://www.shopier.com/birmagaza/45116827": httpx.Response(403),
        }

    def test_each_product_is_fetched_once_and_written_to_all_its_rows(self):
        output, calls = self._run(self.routes)

        self.assertEqual(calls.count(HB_PAGE), 1)
        rows = {row.link.content_id: row for row in LinkProducts.objects.select_related("link")}
        for content_id in (1, 2):
            self.assertEqual(
                (rows[content_id].status, rows[content_id].product_name, rows[content_id].brand),
                ("ok", "Roborock S9 Robot Süpürge", "Roborock"),
            )
            self.assertIsNotNone(rows[content_id].fetched_at)
        self.assertEqual((rows[3].status, rows[3].product_name), ("not_found", ""))
        self.assertEqual(rows[4].status, "blocked")
        # Trendyol is not fetched unless asked for.
        self.assertEqual((rows[5].status, rows[5].fetched_at), ("pending", None))

    def test_second_run_only_retries_when_asked(self):
        self._run(self.routes)

        output, calls = self._run(self.routes)
        self.assertIn("Nothing to fetch", output)
        self.assertEqual(calls, [])

        output, calls = self._run(self.routes, "--retry-failed")
        self.assertEqual(calls, ["https://www.shopier.com/birmagaza/45116827"])

    def test_a_site_that_keeps_refusing_is_left_alone(self):
        for number in range(10, 18):
            make_link(number, f"https://www.shopier.com/birmagaza/{number}000000")
            self.routes[f"https://www.shopier.com/birmagaza/{number}000000"] = httpx.Response(403)
        call_command("extract_products", stdout=StringIO())

        output, calls = self._run(self.routes, "--platform", "shopier")

        self.assertEqual(len(calls), 5)
        self.assertIn("refused 5 times in a row, 4 left untouched", output)
        self.assertEqual(LinkProducts.objects.filter(platform="shopier", status="pending").count(), 4)
