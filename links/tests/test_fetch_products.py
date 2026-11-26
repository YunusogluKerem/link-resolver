import asyncio
from io import StringIO
from unittest import mock

import httpx
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from links.models import LinkDetails, LinkProducts, Links
from links.product_pages import parse_product, product_url

real_sleep = asyncio.sleep  # the tests replace asyncio.sleep to skip the command's pauses

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

    def test_shop_additions_are_removed_from_a_page_title(self):
        titles = {
            "Tchibo Üniversal Soyucu - Fiyatı": "Tchibo Üniversal Soyucu",
            "Tchibo Üniversal Soyucu - Fiyatı, Yorumları": "Tchibo Üniversal Soyucu",
            "Roborock S9 Robot Süpürge - Beyaz Fiyatı": "Roborock S9 Robot Süpürge - Beyaz",
            "Bingo Tablet 40'lı Fiyatı ve Yorumları | Trendyol": "Bingo Tablet 40'lı",
            "Kolye Fiyatları - n11.com": "Kolye",
            "Fiyatı Uygun Mop Seti": "Fiyatı Uygun Mop Seti",  # only at the end
        }
        for title, name in titles.items():
            self.assertEqual(parse_product(f"<title>{title}</title>"), (name, ""), title)

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
            answer = routes[str(request.url)]
            if isinstance(answer, BaseException):
                raise answer
            return answer

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

    def test_interrupted_run_continues_where_it_stopped(self):
        Links.objects.all().delete()  # start from an empty table: only these five products
        urls = [f"https://www.shopier.com/birmagaza/{number}000000" for number in range(21, 26)]
        for number, url in enumerate(urls, start=21):
            make_link(number, url)
        call_command("extract_products", stdout=StringIO())
        page = httpx.Response(200, html='<meta property="og:title" content="Kazak"/>')
        routes = {url: page for url in urls}
        routes[urls[2]] = KeyboardInterrupt()  # Ctrl+C while the third product is being read

        output, calls = self._run(routes, "--platform", "shopier")

        self.assertIn("Interrupted", output)
        self.assertEqual(calls, urls[:3])
        self.assertEqual(LinkProducts.objects.filter(status="ok").count(), 2)  # saved before the interruption
        self.assertEqual(LinkProducts.objects.filter(status="pending").count(), 3)

        routes[urls[2]] = page
        output, calls = self._run(routes, "--platform", "shopier")

        self.assertEqual(calls, urls[2:])  # only the three that were left
        self.assertEqual(LinkProducts.objects.filter(status="ok", product_name="Kazak").count(), 5)

    def test_after_a_refusal_the_pause_is_the_cooldown(self):
        Links.objects.all().delete()
        urls = [f"https://www.shopier.com/birmagaza/{number}000000" for number in range(41, 45)]
        for number, url in enumerate(urls, start=41):
            make_link(number, url)
        call_command("extract_products", stdout=StringIO())
        page = httpx.Response(200, html='<meta property="og:title" content="Kazak"/>')
        routes = {urls[0]: page, urls[1]: httpx.Response(429), urls[2]: page, urls[3]: page}
        sleep = mock.AsyncMock()

        def fake_client(**kwargs) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: routes[str(request.url)]))

        with (
            mock.patch("links.management.commands.fetch_products.build_client", fake_client),
            mock.patch("links.management.commands.fetch_products.asyncio.sleep", sleep),
        ):
            call_command("fetch_products", "--platform", "shopier", "--delay", "2", "--cooldown", "45", stdout=StringIO())

        # normal pause, then the long one after the refused request, then normal again
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [2.0, 45.0, 2.0])
        self.assertEqual(LinkProducts.objects.filter(status="ok").count(), 3)

    def test_every_link_gets_its_first_products_before_any_link_gets_all(self):
        Links.objects.all().delete()
        make_link(51, "https://www.trendyol.com/sr/?mid=1&id=11,12,13,14")
        make_link(52, "https://www.trendyol.com/sr/?mid=2&id=21,22,12")  # 12 is shared with the first link
        call_command("extract_products", stdout=StringIO())
        page = httpx.Response(200, html=JSON_LD)
        ids = ["11", "12", "13", "14", "21", "22"]
        routes = {f"https://www.trendyol.com/x/x-p-{product_id}": page for product_id in ids}
        order = lambda calls: [url.rsplit("-", 1)[1] for url in calls]  # noqa: E731

        _, calls = self._run(routes, "--platform", "trendyol", "--per-link", "2")
        self.assertEqual(order(calls), ["11", "21", "12", "22"])  # two per link, link by link

        _, calls = self._run(routes, "--platform", "trendyol")
        self.assertEqual(order(calls), ["13", "14"])  # the rest; product 12 was read once for both links

    def test_concurrency_runs_that_many_requests_at_once(self):
        Links.objects.all().delete()
        urls = [f"https://www.shopier.com/birmagaza/{number}000000" for number in range(61, 69)]
        for number, url in enumerate(urls, start=61):
            make_link(number, url)
        call_command("extract_products", stdout=StringIO())

        def run(*args: str) -> tuple[int, list[str]]:
            LinkProducts.objects.update(status="pending")
            calls: list[str] = []
            active = peak = 0

            async def handler(request: httpx.Request) -> httpx.Response:
                nonlocal active, peak
                calls.append(str(request.url))
                active += 1
                peak = max(peak, active)
                await real_sleep(0.01)  # the page takes a moment: others start meanwhile
                active -= 1
                return httpx.Response(200, html='<meta property="og:title" content="Kazak"/>')

            def fake_client(**kwargs) -> httpx.AsyncClient:
                return httpx.AsyncClient(transport=httpx.MockTransport(handler))

            with (
                mock.patch("links.management.commands.fetch_products.build_client", fake_client),
                mock.patch("links.management.commands.fetch_products.asyncio.sleep", mock.AsyncMock()),
            ):
                call_command("fetch_products", "--platform", "shopier", *args, stdout=StringIO())
            return peak, calls

        peak, calls = run()
        self.assertEqual(peak, 1)  # the default: one request at a time

        peak, calls = run("--concurrency", "3")
        self.assertEqual(peak, 3)
        self.assertEqual(sorted(calls), urls)  # every product once, none twice
        self.assertEqual(LinkProducts.objects.filter(status="ok").count(), 8)

    def test_proxy_is_used_only_when_asked_for(self):
        seen: list = []
        keepalive: list = []

        def fake_client(**kwargs) -> httpx.AsyncClient:
            seen.append(kwargs.get("proxy"))
            keepalive.append(kwargs.get("keepalive"))
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403)))

        address = "http://user:secret@proxy.example:823"
        with (
            mock.patch("links.management.commands.fetch_products.build_client", fake_client),
            mock.patch("links.management.commands.fetch_products.asyncio.sleep", mock.AsyncMock()),
            mock.patch.dict("os.environ", {"FETCH_PROXY_URL": address}),
        ):
            out = StringIO()
            call_command("fetch_products", "--platform", "shopier", stdout=out)
            call_command("fetch_products", "--platform", "shopier", "--retry-failed", "--proxy", stdout=out)

        self.assertEqual(seen, [None, None, address, address])  # two clients per run: HTTP/2 and HTTP/1.1
        # Through a rotating proxy no connection is reused: every request leaves from another address.
        self.assertEqual(keepalive, [True, True, False, False])
        self.assertNotIn("secret", out.getvalue())  # the address is never printed

    def test_proxy_without_an_address_is_an_error(self):
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(CommandError):
            call_command("fetch_products", "--proxy", stdout=StringIO())

    def test_site_that_refuses_http2_is_read_over_http1_from_then_on(self):
        Links.objects.all().delete()
        make_link(30, "https://www.trendyol.com/sr/?mid=1&id=777,888")
        make_link(31, HB_PAGE)
        call_command("extract_products", stdout=StringIO())
        calls: list[tuple[str, str]] = []

        def fake_client(http2: bool = True, **kwargs) -> httpx.AsyncClient:
            version = "HTTP/2" if http2 else "HTTP/1.1"

            def handler(request: httpx.Request) -> httpx.Response:
                calls.append((request.url.host, version))
                refuse = request.url.host == "www.trendyol.com" and http2
                if refuse:
                    return httpx.Response(403, extensions={"http_version": b"HTTP/2"})
                return httpx.Response(200, html=JSON_LD, extensions={"http_version": version.encode()})

            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        out = StringIO()
        with (
            mock.patch("links.management.commands.fetch_products.build_client", fake_client),
            mock.patch("links.management.commands.fetch_products.asyncio.sleep", mock.AsyncMock()),
        ):
            call_command("fetch_products", "--platform", "trendyol", "--platform", "hepsiburada", stdout=out)

        # HTTP/2 is tried once for Trendyol; after the refusal both products go over HTTP/1.1.
        trendyol = [version for host, version in calls if host == "www.trendyol.com"]
        self.assertEqual(trendyol, ["HTTP/2", "HTTP/1.1", "HTTP/1.1"])
        self.assertIn("HTTP/2 refused, continuing over HTTP/1.1", out.getvalue())
        versions = dict(LinkProducts.objects.values_list("platform", "http_version"))
        self.assertEqual(versions, {"trendyol": "HTTP/1.1", "hepsiburada": "HTTP/2"})
        self.assertEqual(LinkProducts.objects.filter(status="ok").count(), 3)
