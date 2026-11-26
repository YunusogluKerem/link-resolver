from io import StringIO
from unittest import mock

import httpx
from django.core.management import call_command
from django.test import TransactionTestCase

from links.models import LinkDetails, Links


class ResolveLinksCommandTests(TransactionTestCase):
    """The whole command against a fake network. (TransactionTestCase: results are saved from a worker thread.)"""

    def _run(self, routes: dict, *args: str) -> tuple[str, list[str]]:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            answer = routes[str(request.url)]
            if isinstance(answer, list):  # a different answer on each call
                answer = answer.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        def fake_client(**kwargs) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        out = StringIO()
        with mock.patch("links.management.commands.resolve_links.build_client", fake_client):
            call_command("resolve_links", *args, stdout=out)
        return out.getvalue(), calls

    def test_shared_url_is_requested_once_and_failures_are_retried_once(self):
        Links.objects.create(content_id=1, link="https://ty.gl/a")
        Links.objects.create(content_id=2, link="https://ty.gl/a")  # same link, other content
        Links.objects.create(content_id=3, link="https://flaky.example/")
        routes = {
            "https://ty.gl/a": httpx.Response(302, headers={"location": "https://www.trendyol.com/lifecell/urun-p-1"}),
            "https://www.trendyol.com/lifecell/urun-p-1": httpx.Response(403),
            "https://flaky.example/": [httpx.ConnectError("reset"), httpx.Response(200)],
        }

        output, calls = self._run(routes)

        self.assertEqual(calls.count("https://ty.gl/a"), 1)
        self.assertIn("Retrying 1 failed urls once", output)
        details = {detail.link.content_id: detail for detail in LinkDetails.objects.select_related("link")}
        self.assertEqual([details[n].status for n in (1, 2, 3)], ["resolved", "resolved", "ok"])
        self.assertEqual(details[2].link_detail, "https://www.trendyol.com/lifecell/urun-p-1")
        self.assertEqual((details[1].brand_exist, details[1].brand_name), (True, "Lifecell"))

        # Running again finds nothing left to do and makes no request.
        output, calls = self._run(routes)
        self.assertIn("Nothing to resolve", output)
        self.assertEqual(calls, [])

    def test_rate_limited_url_is_retried_at_the_end(self):
        Links.objects.create(content_id=1, link="https://busy.example/")
        # Three 429s use up the resolver's own retries; the final pass gets the page.
        routes = {"https://busy.example/": [httpx.Response(429)] * 3 + [httpx.Response(200)]}

        with mock.patch("links.resolver.asyncio.sleep", mock.AsyncMock()):  # skip the backoff waits
            output, calls = self._run(routes)

        self.assertIn("Retrying 1 failed urls once", output)
        self.assertEqual(len(calls), 4)
        self.assertEqual(LinkDetails.objects.get().status, "ok")
