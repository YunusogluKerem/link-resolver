import httpx
from django.test import SimpleTestCase

from links.resolver import LinkResolver, find_client_redirect, find_site_name


def redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


def html_page(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html"}, text=body)


class ResolverTests(SimpleTestCase):
    """The network is replaced by a dict of url -> response (or exception)."""

    async def _resolve(self, url: str, routes: dict) -> tuple:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            answer = routes[str(request.url)]
            if isinstance(answer, list):  # a different answer on each call
                answer = answer.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await LinkResolver(client, backoff=0).resolve(url)
        return result, calls

    async def test_follows_redirects_and_records_the_chain(self):
        result, _ = await self._resolve(
            "https://ty.gl/abc",
            {
                "https://ty.gl/abc": redirect("https://x.adj.st/abc"),
                "https://x.adj.st/abc": redirect("https://shop.example/urun"),
                "https://shop.example/urun": html_page("<html>ok</html>"),
            },
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.final_url, "https://shop.example/urun")
        self.assertEqual([hop["status"] for hop in result.chain], [302, 302, 200])

    async def test_site_name_of_the_final_page_is_stored_on_the_last_hop(self):
        page = '<html><head><meta property="og:site_name" content="Ak&ccedil;a Butik"></head></html>'
        result, _ = await self._resolve(
            "https://kisa.example/a",
            {"https://kisa.example/a": redirect("https://shop.example/"), "https://shop.example/": html_page(page)},
        )
        self.assertEqual(result.chain[-1]["site_name"], "Akça Butik")
        self.assertNotIn("site_name", result.chain[0])

    async def test_403_after_a_redirect_is_resolved_with_the_final_url(self):
        result, _ = await self._resolve(
            "https://ty.gl/abc",
            {
                "https://ty.gl/abc": redirect("https://www.trendyol.com/magaza/x-m-1"),
                "https://www.trendyol.com/magaza/x-m-1": httpx.Response(403),
            },
        )
        self.assertEqual((result.status, result.status_code), ("resolved", 403))
        self.assertEqual(result.final_url, "https://www.trendyol.com/magaza/x-m-1")

    async def test_403_on_the_first_request_is_blocked_without_a_final_url(self):
        result, _ = await self._resolve("https://app.hb.biz/abc", {"https://app.hb.biz/abc": httpx.Response(403)})
        self.assertEqual((result.status, result.final_url), ("blocked", ""))

    async def test_timeout_becomes_a_status_and_keeps_the_chain(self):
        result, _ = await self._resolve(
            "https://a.example/1",
            {
                "https://a.example/1": redirect("https://b.example/2"),
                "https://b.example/2": httpx.ReadTimeout("slow"),
            },
        )
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.final_url, "https://b.example/2")  # known from the Location header
        self.assertIn("ReadTimeout", result.chain[-1]["error"])

    async def test_network_error_does_not_raise(self):
        result, _ = await self._resolve("https://gone.example/", {"https://gone.example/": httpx.ConnectError("dns")})
        self.assertEqual((result.status, result.final_url, result.status_code), ("error", "", None))

    async def test_429_is_retried(self):
        result, calls = await self._resolve(
            "https://a.example/", {"https://a.example/": [httpx.Response(429), html_page("ok")]}
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(calls), 2)

    async def test_meta_refresh_is_followed(self):
        page = '<html><head><meta http-equiv="refresh" content="0; url=/hedef"></head></html>'
        result, _ = await self._resolve(
            "https://a.example/kisa",
            {"https://a.example/kisa": html_page(page), "https://a.example/hedef": html_page("ok")},
        )
        self.assertEqual(result.final_url, "https://a.example/hedef")
        self.assertTrue(result.chain[0]["client_redirect"])


class FindClientRedirectTests(SimpleTestCase):
    BASE = "https://a.example/page"

    def test_js_redirect_on_a_tiny_page(self):
        page = '<script>window.location.href = "https://b.example/x";</script>'
        self.assertEqual(find_client_redirect(page, self.BASE), "https://b.example/x")

    def test_js_assignment_on_a_real_page_is_ignored(self):
        page = '<script>window.location.href = "https://b.example/x";</script>' + "<p>content</p>" * 2000
        self.assertIsNone(find_client_redirect(page, self.BASE))

    def test_redirect_back_to_the_same_page_is_ignored(self):
        page = '<script>window.location.href = "/page?bm-verify=TOKEN";</script>'
        self.assertIsNone(find_client_redirect(page, self.BASE))

    def test_slow_self_reload_and_noscript_fallback_are_ignored(self):
        reload_page = '<meta http-equiv="refresh" content="300; url=https://a.example/other">'
        noscript = '<noscript><meta http-equiv="refresh" content="0; url=/?_fb_noscript=1"></noscript>'
        self.assertIsNone(find_client_redirect(reload_page, self.BASE))
        self.assertIsNone(find_client_redirect(noscript, self.BASE))


class FindSiteNameTests(SimpleTestCase):
    def test_reads_og_site_name_in_either_attribute_order(self):
        self.assertEqual(find_site_name('<meta property="og:site_name" content="ZARA">'), "ZARA")
        self.assertEqual(find_site_name("<meta content='Seda  Atelier' property='og:site_name' />"), "Seda Atelier")

    def test_other_meta_tags_are_ignored(self):
        self.assertEqual(find_site_name('<meta property="og:title" content="Kazak"><title>x</title>'), "")
        self.assertEqual(find_site_name(""), "")
