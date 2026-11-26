from io import StringIO
from unittest import mock

import httpx
from django.core.management import call_command
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from links.models import LinkDetails, Links, Stores
from links.stores import parse_store, seller_id, store_brand, store_url

VERIFIED = '"markers":[{"url":"https:\\u002F\\u002Fcdn.example\\u002Fverified-seller-marker.svg"}]'


def store_page(name: str, brands: list[str], verified: bool = False) -> str:
    """A page shaped like Trendyol's: the seller block in a script, then one card per product."""
    marks = "," + VERIFIED if verified else ""
    seller = f'{{"id":1,"name":"{name}","officialName":"X A.Ş.","productCount":9{marks}}}'
    cards = "".join(f'{{"brandName":"{brand}","x":1,"brandName":"{brand}"}}' for brand in brands)
    return f'<html><script>window["__ss-header__PROPS"]={{"data":{{"store":{{"meta":{{"seller":{seller}}}}}}}}}</script>{cards}</html>'


class SellerIdTests(SimpleTestCase):
    def test_store_page_search_page_and_deep_link(self):
        self.assertEqual(seller_id("https://www.trendyol.com/magaza/x-m-982060?adjust_t=abc"), "982060")
        self.assertEqual(seller_id("https://www.trendyol.com/sr/?mid=417967&id=1,2"), "417967")
        chain = [{"url": "https://p8zh.adj.st/t?Page=SellerStore&MerchantId=381961", "status": 302}]
        self.assertEqual(seller_id("https://ty.gl/abc", chain), "381961")

    def test_links_without_a_seller(self):
        self.assertIsNone(seller_id("https://www.trendyol.com/lifecell/urun-p-1191242708"))
        self.assertIsNone(seller_id("https://www.hepsiburada.com/magaza/estee-lauder"))
        self.assertIsNone(seller_id("https://www.akcabutik.com/sr?mid=5"))  # `mid` on an ordinary site

    def test_store_address_is_built_from_the_id(self):
        self.assertEqual(store_url("982060"), "https://www.trendyol.com/magaza/x-m-982060")


class ParseStoreTests(SimpleTestCase):
    def test_name_verified_mark_and_product_brands(self):
        page = store_page("VICTORIA’S SECRET", ["Victoria's Secret"] * 3 + ["PINK"], verified=True)
        self.assertEqual(parse_store(page), ("VICTORIA’S SECRET", True, {"Victoria's Secret": 3, "PINK": 1}))

    def test_seller_without_the_mark(self):
        self.assertEqual(parse_store(store_page("Moda Dükkanı", ["Koton"])), ("Moda Dükkanı", False, {"Koton": 1}))

    def test_page_without_a_seller_block(self):
        self.assertEqual(parse_store("<html><title>Trendyol</title></html>"), ("", False, {}))


class StoreBrandTests(SimpleTestCase):
    def test_store_named_after_a_listed_brand(self):
        self.assertEqual(store_brand("Koton", False, {}), ("Koton", "listed"))
        self.assertEqual(store_brand("ESTEE LAUDER Türkiye", False, {}), ("Estée Lauder", "listed"))

    def test_store_named_after_the_brand_of_its_products(self):
        products = {"Yves Saint Laurent": 71, "Lancôme": 2}
        self.assertEqual(store_brand("YVES SAINT LAURENT BEAUTY", False, products), ("Yves Saint Laurent", "products"))

    def test_verified_store_counts_under_its_own_name(self):
        self.assertEqual(store_brand("Örnek Kozmetik", True, {}), ("Örnek Kozmetik", "verified"))

    def test_store_name_alone_is_not_a_brand(self):
        self.assertEqual(store_brand("OG Store", False, {}), (None, ""))
        # One brand on the shelves is not enough: the store is not named after it.
        self.assertEqual(store_brand("Moda Dükkanı", False, {"Koton": 20}), (None, ""))
        # Named after a brand that is a small part of what it sells.
        self.assertEqual(store_brand("Rowenta Outlet", False, {"English Home": 52, "Rowenta": 3}), (None, ""))
        self.assertEqual(store_brand("", True, {"Koton": 5}), (None, ""))


def make_link(content_id: int, final_url: str) -> LinkDetails:
    link = Links.objects.create(content_id=content_id, link=f"https://ty.gl/{content_id}")
    return LinkDetails.objects.create(
        link=link, link_detail=final_url, redirect_chain=[], status=LinkDetails.Status.RESOLVED,
        status_code=403, resolved_at=timezone.now(),
    )  # fmt: skip


class FetchStoresCommandTests(TransactionTestCase):
    """The whole command against a fake network. (TransactionTestCase: results are saved from a worker thread.)"""

    def setUp(self):
        make_link(1, "https://www.trendyol.com/magaza/x-m-20?adjust_t=a")  # a brand's own store, two links
        make_link(2, "https://www.trendyol.com/sr/?mid=20&id=5,6")
        make_link(3, "https://www.trendyol.com/magaza/x-m-10")  # a multi-brand seller, one link
        make_link(4, "https://www.trendyol.com/magaza/x-m-30?utm_campaign=elidor_ornek_AbC12")
        make_link(5, "https://www.youtube.com/watch?v=abc")
        self.routes = {
            "https://www.trendyol.com/magaza/x-m-20": httpx.Response(200, html=store_page("Örnek Marka", [], True)),
            "https://www.trendyol.com/magaza/x-m-10": httpx.Response(200, html=store_page("OG Store", ["A", "B"])),
            "https://www.trendyol.com/magaza/x-m-30": httpx.Response(200, html=store_page("Toptancı", [], True)),
        }

    def _run(self, *args: str, routes: dict | None = None) -> tuple[str, list[str]]:
        calls: list[str] = []
        routes = routes or self.routes

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
            mock.patch("links.management.commands.fetch_stores.build_client", fake_client),
            mock.patch("links.management.commands.fetch_stores.asyncio.sleep", mock.AsyncMock()),
        ):
            call_command("fetch_stores", *args, stdout=out)
        return out.getvalue(), calls

    def test_each_seller_is_read_once_busiest_first_and_a_second_run_does_nothing(self):
        output, calls = self._run()

        self.assertIn("Sellers in the links: 3 (3 new)", output)
        self.assertEqual(calls[0], "https://www.trendyol.com/magaza/x-m-20")  # two links point at it
        self.assertEqual(len(calls), 3)
        store = Stores.objects.get(seller_id="20")
        self.assertEqual((store.name, store.verified, store.link_count, store.status), ("Örnek Marka", True, 2, "ok"))

        output, calls = self._run()
        self.assertIn("Nothing to read", output)
        self.assertEqual(calls, [])

    def test_detect_brands_uses_the_store_only_where_the_url_names_nothing(self):
        self._run()
        call_command("detect_brands", stdout=StringIO())

        brands = dict(LinkDetails.objects.values_list("link__content_id", "brand_name"))
        self.assertEqual(brands[1], "Örnek Marka")  # the store page
        self.assertEqual(brands[2], "Örnek Marka")  # a product list of the same seller
        self.assertIsNone(brands[3])  # a store name alone is not a brand
        self.assertEqual(brands[4], "Elidor")  # the URL names a listed brand: it wins over the store

    def test_interrupted_run_continues_where_it_stopped(self):
        routes = dict(self.routes)
        routes["https://www.trendyol.com/magaza/x-m-10"] = KeyboardInterrupt()

        output, calls = self._run(routes=routes)
        self.assertIn("Interrupted", output)
        self.assertEqual(Stores.objects.filter(status="ok").count(), 1)

        output, calls = self._run()
        self.assertEqual(len(calls), 2)  # only the two that were left
        self.assertEqual(Stores.objects.filter(status="ok").count(), 3)

    def test_refusals_slow_the_run_down_and_five_in_a_row_stop_it(self):
        for number in range(40, 48):
            make_link(number, f"https://www.trendyol.com/magaza/x-m-{number}")
        routes = {f"https://www.trendyol.com/magaza/x-m-{n}": httpx.Response(429) for n in (10, 20, 30, *range(40, 48))}
        sleep = mock.AsyncMock()

        def fake_client(**kwargs) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: routes[str(request.url)]))

        out = StringIO()
        with (
            mock.patch("links.management.commands.fetch_stores.build_client", fake_client),
            mock.patch("links.management.commands.fetch_stores.asyncio.sleep", sleep),
        ):
            call_command("fetch_stores", "--delay", "2.5", "--cooldown", "30", stdout=out)

        self.assertIn("refused 5 times in a row, 6 stores left untouched", out.getvalue())
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [30.0] * 4)  # the long pause after a refusal
        self.assertEqual(Stores.objects.filter(status="pending").count(), 6)
