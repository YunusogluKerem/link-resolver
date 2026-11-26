from django.test import SimpleTestCase

from links.brands import detect_brand, marketplace_brand_id, page_site_name, site_category


class DetectBrandTests(SimpleTestCase):
    def test_trendyol_product_brand_is_the_first_segment(self):
        url = "https://www.trendyol.com/lifecell/guava-ekstresi-iceren-takviye-280g-p-1191242708?boutiqueId=61"
        self.assertEqual(detect_brand(url), "Lifecell")

    def test_trendyol_store_and_search_pages_have_no_brand(self):
        self.assertIsNone(detect_brand("https://www.trendyol.com/magaza/x-m-123456?adjust_t=abc"))
        self.assertIsNone(detect_brand("https://www.trendyol.com/sr/?mid=417967&id=1239478694"))

    def test_hepsiburada_product_brand_must_be_a_listed_brand_at_the_start_of_the_slug(self):
        url = "https://www.hepsiburada.com/bingo-akilli-kapsul-bulasik-makinesi-tableti-40-li-pm-HBC00006V9KXM"
        self.assertEqual(detect_brand(url), "Bingo")
        self.assertEqual(detect_brand("https://www.hepsiburada.com/estee-lauder-serum-50-ml-p-HBV00000ABC"), "Estée Lauder")
        # Unknown first word, and a known brand that is not at the start ("compatible with").
        self.assertIsNone(detect_brand("https://www.hepsiburada.com/kadin-siyah-deri-ceket-p-HBV00000XYZ"))
        self.assertIsNone(detect_brand("https://www.hepsiburada.com/telefon-kilifi-samsung-uyumlu-p-HBV00000XYZ"))

    def test_store_name_counts_only_when_it_is_a_known_brand(self):
        self.assertEqual(detect_brand("https://www.hepsiburada.com/magaza/estee-lauder?ftid=1"), "Estée Lauder")
        self.assertIsNone(detect_brand("https://www.hepsiburada.com/magaza/og-store"))
        self.assertEqual(detect_brand("https://www.trendyol.com/magaza/nike-m-123456"), "Nike")

    def test_marketplace_name_is_never_the_brand(self):
        self.assertIsNone(detect_brand("https://www.hepsiburada.com/kampanyalar/hepsiburada-qer1yufdjl54g"))
        self.assertIsNone(detect_brand("https://www.amazon.de/dp/B09SHDLMCP/ref=cm_sw_r"))
        self.assertIsNone(detect_brand("https://apps.apple.com/tr/app/bir-uygulama/id123456"))

    def test_brand_list_matches_whole_words_only(self):
        self.assertEqual(detect_brand("https://www.eveshop.com.tr/collections/flormar-indirimi"), "Flormar")
        self.assertEqual(detect_brand("https://www.hepsiburada.com/ara?q=mudo+home&ftid=1"), "Mudo")
        self.assertIsNone(detect_brand("https://www.n11.com/urun/macbook-kilifi"))  # "mac" is not "macbook"

    def test_everyday_word_brand_counts_only_at_the_start_of_a_marketplace_name(self):
        self.assertEqual(detect_brand("https://www.hepsiburada.com/magaza/mavi?ftid=1"), "Mavi")
        self.assertEqual(detect_brand("https://www.hepsiburada.com/ara?q=mavi+boxer"), "Mavi")
        self.assertEqual(detect_brand("https://www.hepsiburada.com/mavi-7li-siyah-basic-boxer-p-HBCV0000CJSDY4"), "Mavi")
        # "mavi" as the colour blue: middle of a slug, or on a boutique's own site.
        self.assertIsNone(detect_brand("https://www.hepsiburada.com/kampanyalar/bebe-mavi-kazaklar"))
        self.assertEqual(detect_brand("https://www.dumanbutik.com/mavi-triko-kazak"), "Dumanbutik")

    def test_campaign_parameter_is_searched(self):
        url = "https://www.trendyol.com/sr?mid=968&utm_campaign=elidor_ornekhesap_AbC12"
        self.assertEqual(detect_brand(url), "Elidor")

    def test_brand_own_domain_counts(self):
        self.assertEqual(detect_brand("https://www.oxxo.com.tr/yeni-sezon"), "Oxxo")


class OwnSiteBrandTests(SimpleTestCase):
    """Rule 4: a site that is not on the non-brand list is a brand's own website."""

    def test_brand_is_derived_from_the_domain_name(self):
        self.assertEqual(detect_brand("https://www.akcabutik.com/urun/triko-kazak"), "Akcabutik")
        self.assertEqual(detect_brand("https://kubbe.store/products/1"), "Kubbe")
        self.assertEqual(detect_brand("https://www.coca-cola.com/tr/tr"), "Coca Cola")

    def test_subdomains_and_country_endings_are_not_the_name(self):
        self.assertEqual(detect_brand("https://de.checkout.gymshark.com/cart"), "Gymshark")
        self.assertEqual(detect_brand("https://www.seymacoskun.com.tr/"), "Seymacoskun")
        self.assertEqual(detect_brand("https://t-max.co.uk/shop"), "T Max")

    def test_the_brand_list_spelling_wins_over_the_raw_domain(self):
        self.assertEqual(detect_brand("https://www2.hm.com/tr_tr/index.html"), "H&M")
        self.assertEqual(detect_brand("https://shop.mango.com/tr"), "Mango")

    def test_page_site_name_is_preferred_over_the_domain(self):
        url = "https://sedayalcinatelier.com/products/pantolon"
        self.assertEqual(detect_brand(url), "Sedayalcinatelier")
        self.assertEqual(detect_brand(url, site_name="Seda Yalçın Atelier"), "Seda Yalçın Atelier")
        # A slogan instead of a name is ignored.
        slogan = "Türkiye'nin en büyük online butik alışveriş sitesi ve moda adresi"
        self.assertEqual(detect_brand(url, site_name=slogan), "Sedayalcinatelier")
        # So is a site name that only repeats the address.
        self.assertEqual(detect_brand(url, site_name="sedayalcinatelier.com"), "Sedayalcinatelier")

    def test_non_brand_domains_have_no_brand(self):
        for url in [
            "https://www.youtube.com/watch?v=abc",  # social
            "https://www.cnnturk.com/turkiye/haber-123",  # news
            "https://linktr.ee/birisi",  # link-in-bio
            "https://ty.gl/abc123",  # shortener that could not be followed
            "https://www.bubilet.com.tr/istanbul/etkinlik/konser",  # ticket platform
            "https://turkiye.unwomen.org/tr",  # organization
            "https://www.amazon.com.tr/dp/B0ABC",  # marketplace
        ]:
            self.assertIsNone(detect_brand(url, site_name="Some Site"), url)

    def test_multi_brand_retailer_is_not_a_brand_but_a_listed_brand_in_its_url_counts(self):
        self.assertIsNone(detect_brand("https://www.boyner.com.tr/kadin-ayakkabi-c-1001"))
        self.assertEqual(detect_brand("https://www.boyner.com.tr/kiko-milano-m-2002710390"), "KIKO Milano")

    def test_store_chain_is_a_retailer_not_a_brand(self):
        self.assertIsNone(detect_brand("https://www.sephora.com.tr/sephora-kampanyalar-hediye.html"))
        self.assertIsNone(detect_brand("https://apps.apple.com/tr/app/sephora-parfum-kozmetik/id560058271"))

    def test_gambling_and_unclear_sites_have_no_brand(self):
        self.assertIsNone(detect_brand("https://1xmatches.online/ru/"))
        self.assertIsNone(detect_brand("https://otvet.co/goods/productions/abc/"))

    def test_a_listed_brand_in_a_news_url_still_counts(self):
        self.assertEqual(detect_brand("https://www.cnnturk.com/teknoloji/samsung-yeni-telefon"), "Samsung")

    def test_site_category_patterns(self):
        self.assertEqual(site_category("m.youtube.com"), "social")  # subdomain of a listed domain
        self.assertEqual(site_category("link.amazon"), "marketplace")  # `amazon.*`: any ending
        self.assertEqual(site_category("play.google.com"), "marketplace")  # more specific than google.com
        self.assertEqual(site_category("docs.google.com"), "platform")
        self.assertEqual(site_category("ttf.org.tr"), "organization")  # a whole ending
        self.assertIsNone(site_category("max.com"))  # not a subdomain of x.com
        self.assertIsNone(site_category("www.akcabutik.com"))

    def test_page_site_name_reads_the_last_hop(self):
        chain = [{"url": "https://a.example/", "status": 302}, {"url": "https://b.example/", "status": 200, "site_name": "B"}]
        self.assertEqual(page_site_name(chain), "B")
        self.assertEqual(page_site_name(chain[:1]), "")
        self.assertEqual(page_site_name([]), "")


class MarketplaceBrandIdTests(SimpleTestCase):
    def test_reads_brand_ids_from_the_adj_st_hop(self):
        chain = [
            {"url": "https://ty.gl/abc", "status": 302},
            {"url": "https://p8zh.adj.st/token?Page=Search&MerchantId=968&BrandIds=617%2C101640", "status": 302},
            {"url": "https://www.trendyol.com/sr?mid=968&wb=617,101640", "status": 403},
        ]
        self.assertEqual(marketplace_brand_id(chain), "617,101640")

    def test_empty_when_the_chain_has_no_brand_id(self):
        chain = [
            {"url": "https://ty.gl/abc", "status": 302},
            {"url": "https://p8zh.adj.st/token?Page=SellerStore&MerchantId=123456&BrandIds=", "status": 302},
        ]
        self.assertEqual(marketplace_brand_id(chain), "")
        self.assertEqual(marketplace_brand_id([]), "")
