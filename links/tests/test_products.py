from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from links.models import LinkDetails, LinkProducts, Links
from links.products import ProductRef, extract_products, product_brands

TRENDYOL_LIST = "https://www.trendyol.com/sr/?mid=417967&id=111,222,333&adjust_t=abc"
DEEP_LINK = "https://p8zh.adj.st/token?Page=SellerStore&MerchantId=417967&ContentIds=111%2C222%2C333"


class ExtractProductsTests(SimpleTestCase):
    def test_product_page_has_its_id_in_the_path(self):
        cases = {
            "https://www.trendyol.com/lifecell/guava-takviye-280g-p-1191242708?boutiqueId=61": ("trendyol", "1191242708"),
            "https://www.trendyol-milla.com/Trendyolmilla/gecelik-p-1073369214": ("trendyol", "1073369214"),
            "https://www.hepsiburada.com/roborock-s9-robot-supurge-p-HBCV0000EUMLOS?magaza=x": ("hepsiburada", "HBCV0000EUMLOS"),
            "https://www.hepsiburada.com/bingo-tablet-40-li-pm-HBC00006V9KXM": ("hepsiburada", "HBC00006V9KXM"),
            "https://www.hepsiburada.com/selpak-havlu-12-li-pm-zyeczaci9671015": ("hepsiburada", "zyeczaci9671015"),
            "https://www.amazon.com.tr/Sol-Janeiro-Seti/dp/B0B4HKNDLQ?tag=x": ("amazon", "B0B4HKNDLQ"),
            "https://www.amazon.de/dp/B09SHDLMCP/ref=cm_sw_r": ("amazon", "B09SHDLMCP"),
            "https://www.shopier.com/birmagaza/45116827": ("shopier", "45116827"),
        }
        for url, (platform, product_id) in cases.items():
            self.assertEqual(extract_products(url), [ProductRef(platform, product_id, "path")], url)

    def test_trendyol_list_gives_one_product_per_id(self):
        refs = extract_products(TRENDYOL_LIST)
        self.assertEqual([ref.product_id for ref in refs], ["111", "222", "333"])
        self.assertEqual({(ref.platform, ref.source) for ref in refs}, {("trendyol", "query")})

    def test_final_url_wins_over_the_chain(self):
        chain = [{"url": "https://ty.gl/abc", "status": 302}, {"url": DEEP_LINK, "status": 302}]
        refs = extract_products(TRENDYOL_LIST, chain)
        self.assertEqual({ref.source for ref in refs}, {"query"})
        self.assertEqual(len(refs), 3)

    def test_chain_is_used_when_the_final_url_has_no_ids(self):
        chain = [{"url": DEEP_LINK, "status": 302}]
        refs = extract_products("https://ty.gl/abc", chain)
        self.assertEqual([(ref.platform, ref.product_id, ref.source) for ref in refs][0], ("trendyol", "111", "chain"))
        hb_chain = [{"url": "https://7t4g.adj.st/?adj_t=1&sku=HBCV0000EUMLOS", "status": 302}]
        self.assertEqual(extract_products("", hb_chain), [ProductRef("hepsiburada", "HBCV0000EUMLOS", "chain")])

    def test_pages_without_a_product_give_nothing(self):
        for url in (
            "https://www.trendyol.com/magaza/x-m-123456",
            "https://www.hepsiburada.com/magaza/estee-lauder",
            "https://www.amazon.com.tr/blackfriday",
            "https://www.youtube.com/watch?v=abc",
            # `sku` and `id` on an ordinary site are that site's own business.
            "https://www.akcabutik.com/urun?sku=12345678&id=5",
        ):
            self.assertEqual(extract_products(url, [{"url": url, "status": 200}]), [], url)


def make_detail(content_id: int, final_url: str) -> LinkDetails:
    link = Links.objects.create(content_id=content_id, link=f"https://ty.gl/{content_id}")
    return LinkDetails.objects.create(
        link=link, link_detail=final_url, redirect_chain=[], status=LinkDetails.Status.OK, resolved_at=timezone.now()
    )


class ExtractProductsCommandTests(TestCase):
    def run_command(self) -> str:
        out = StringIO()
        call_command("extract_products", stdout=out)
        return out.getvalue()

    def test_fills_the_table_and_a_second_run_changes_nothing(self):
        make_detail(1, TRENDYOL_LIST)
        make_detail(2, "https://www.hepsiburada.com/bingo-tablet-pm-HBC00006V9KXM")
        make_detail(3, "https://www.trendyol.com/magaza/x-m-123456")

        self.assertIn("Added: 4", self.run_command())
        self.assertEqual(LinkProducts.objects.count(), 4)
        self.assertEqual(LinkProducts.objects.filter(platform="trendyol", source="query").count(), 3)

        self.assertIn("Added: 0   Source changed: 0   Removed: 0", self.run_command())
        self.assertEqual(LinkProducts.objects.count(), 4)

    def test_rows_the_link_no_longer_has_are_removed(self):
        detail = make_detail(1, TRENDYOL_LIST)
        self.run_command()
        detail.link_detail = "https://www.trendyol.com/sr/?mid=417967&id=111"
        detail.save()

        self.assertIn("Removed: 2", self.run_command())
        self.assertEqual(list(LinkProducts.objects.values_list("product_id", flat=True)), ["111"])


class ProductBrandsTests(TestCase):
    def setUp(self):
        self.detail = make_detail(1, TRENDYOL_LIST)  # three products: 111, 222, 333
        self.other = make_detail(2, "https://www.hepsiburada.com/bingo-tablet-pm-HBC00006V9KXM")
        call_command("extract_products", stdout=StringIO())

    def read(self, brands: dict[str, str]) -> None:
        for product_id, brand in brands.items():
            LinkProducts.objects.filter(product_id=product_id).update(status="ok", brand=brand)

    def test_brand_of_at_least_half_of_the_products_stands_for_the_link(self):
        self.read({"111": "Elidor", "222": "Elidor", "333": "Dove"})
        self.assertEqual(product_brands(), {self.detail.link_id: "Elidor"})  # the link without a brand is absent

    def test_more_mixed_link_has_no_brand(self):
        self.read({"111": "Elidor", "222": "Dove", "333": "Signal"})
        self.assertEqual(product_brands(), {self.detail.link_id: ""})

    def test_products_read_without_a_brand_count_against_the_majority(self):
        self.read({"111": "Elidor", "222": "", "333": ""})
        self.assertEqual(product_brands(), {self.detail.link_id: ""})

    def test_products_not_read_yet_are_left_out_of_the_majority(self):
        self.read({"111": "Elidor"})  # 222 and 333 are still pending; 1 of 3 read is enough for a small link
        self.assertEqual(product_brands(), {self.detail.link_id: "Elidor"})

    def test_too_few_products_read_leaves_the_link_to_the_url_rules(self):
        ids = [str(number) for number in range(1001, 1021)]  # 20 products: 3 must be read
        big = make_detail(3, "https://www.trendyol.com/sr/?mid=968&id=" + ",".join(ids))
        call_command("extract_products", stdout=StringIO())

        self.read({ids[0]: "Elidor", ids[1]: "Elidor"})
        self.assertNotIn(big.link_id, product_brands())
        self.read({ids[2]: "Elidor"})
        self.assertEqual(product_brands()[big.link_id], "Elidor")

    def test_small_link_needs_a_fifth_of_its_products(self):
        ids = [str(number) for number in range(2001, 2011)]  # 10 products: 2 must be read
        medium = make_detail(4, "https://www.trendyol.com/sr/?mid=968&id=" + ",".join(ids))
        call_command("extract_products", stdout=StringIO())

        self.read({ids[0]: "Dove"})
        self.assertNotIn(medium.link_id, product_brands())
        self.read({ids[1]: "Dove"})
        self.assertEqual(product_brands()[medium.link_id], "Dove")

    def test_product_name_gives_the_brand_when_the_page_states_none(self):
        LinkProducts.objects.filter(product_id="111").update(status="ok", product_name="Dove Nemlendirici Krem")
        LinkProducts.objects.filter(product_id="222").update(status="ok", product_name="Mavi Triko Kazak")
        self.assertEqual(product_brands(), {self.detail.link_id: "Dove"})  # 1 of the 2 read products

    def test_brand_stated_by_the_page_beats_the_product_name(self):
        LinkProducts.objects.filter(product_id="111").update(status="ok", brand="ELIDOR", product_name="Dove Şampuan")
        self.assertEqual(product_brands(), {self.detail.link_id: "Elidor"})  # and in the list's spelling

    def test_mixed_link_gets_brand_exist_false(self):
        self.read({"111": "Elidor", "222": "Dove", "333": "Signal"})
        LinkDetails.objects.filter(pk=self.detail.pk).update(brand_exist=True, brand_name="Elidor")
        call_command("detect_brands", stdout=StringIO())
        self.detail.refresh_from_db()
        self.assertEqual((self.detail.brand_exist, self.detail.brand_name), (False, None))


class DetectBrandsWithProductsTests(TestCase):
    def test_detect_brands_takes_the_brand_from_the_product_page(self):
        detail = make_detail(1, "https://www.hepsiburada.com/akilli-robot-supurge-beyaz-p-HBCV0000EUMLOS")
        call_command("extract_products", stdout=StringIO())
        call_command("detect_brands", stdout=StringIO())
        detail.refresh_from_db()
        self.assertFalse(detail.brand_exist)

        LinkProducts.objects.update(status="ok", brand="Roborock")
        call_command("detect_brands", stdout=StringIO())
        detail.refresh_from_db()
        self.assertEqual((detail.brand_exist, detail.brand_name), (True, "Roborock"))


class LinkProductsApiTests(TestCase):
    URL = "/api/link-products/"

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser("admin", password="admin-pass")
        make_detail(101, TRENDYOL_LIST)
        make_detail(102, "https://www.hepsiburada.com/bingo-tablet-pm-HBC00006V9KXM")
        call_command("extract_products", stdout=StringIO())

    def setUp(self):
        self.client = APIClient()

    def _login(self) -> None:
        response = self.client.post("/api/token/", {"username": "admin", "password": "admin-pass"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

    def test_without_token_is_401(self):
        self.assertEqual(self.client.get(self.URL).status_code, 401)

    def test_admin_gets_products_with_pagination_fields(self):
        self._login()
        with self.assertNumQueries(3):  # user, count, page joined with Links
            body = self.client.get(self.URL).json()
        self.assertEqual((body["count"], body["total_pages"], body["remaining_pages"]), (4, 1, 0))
        self.assertEqual(
            {key: body["results"][0][key] for key in ("content_id", "platform", "product_id", "source")},
            {"content_id": 101, "platform": "trendyol", "product_id": "111", "source": "query"},
        )

    def test_platform_filter(self):
        self._login()
        body = self.client.get(self.URL, {"platform": "hepsiburada"}).json()
        self.assertEqual([row["product_id"] for row in body["results"]], ["HBC00006V9KXM"])
