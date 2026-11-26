from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from links.api import LinkDetailPagination
from links.models import LinkDetails, Links

URL = "/api/link-details/"


class LinkDetailsApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        User.objects.create_superuser("admin", password="admin-pass")
        User.objects.create_user("regular", password="regular-pass")
        for number in range(1, 4):
            link = Links.objects.create(content_id=100 + number, link=f"https://ty.gl/{number}")
            LinkDetails.objects.create(
                link=link,
                link_detail=f"https://www.trendyol.com/lifecell/urun-p-{number}",
                brand_exist=True,
                brand_name="Lifecell",
                redirect_chain=[{"url": link.link, "status": 302}],
                status=LinkDetails.Status.OK,
                status_code=200,
                resolved_at=timezone.now(),
            )

    def setUp(self):
        self.client = APIClient()

    def _login(self, username: str, password: str) -> None:
        response = self.client.post("/api/token/", {"username": username, "password": password})
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

    def test_without_token_is_401(self):
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_wrong_password_gets_no_token(self):
        response = self.client.post("/api/token/", {"username": "admin", "password": "wrong"})
        self.assertEqual(response.status_code, 401)

    def test_non_admin_token_is_403(self):
        self._login("regular", "regular-pass")
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_admin_gets_a_page_with_pagination_fields(self):
        self._login("admin", "admin-pass")
        # 3 queries however many rows: the user (JWT), the count, the page joined with Links.
        with self.assertNumQueries(3):
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            {key: body[key] for key in ("count", "total_pages", "current_page", "remaining_pages", "next", "previous")},
            {"count": 3, "total_pages": 1, "current_page": 1, "remaining_pages": 0, "next": None, "previous": None},
        )
        first = body["results"][0]
        self.assertEqual(first["link_id"], Links.objects.get(content_id=101).id)
        self.assertEqual((first["content_id"], first["link"]), (101, "https://ty.gl/1"))
        self.assertEqual((first["brand_exist"], first["brand_name"], first["status"]), (True, "Lifecell", "ok"))

    def test_remaining_pages_counts_down(self):
        self._login("admin", "admin-pass")
        with mock.patch.object(LinkDetailPagination, "page_size", 2):
            first = self.client.get(URL).json()
            second = self.client.get(URL, {"page": 2}).json()
        self.assertEqual((first["total_pages"], first["current_page"], first["remaining_pages"]), (2, 1, 1))
        self.assertTrue(first["next"].endswith("?page=2"))
        self.assertEqual((second["current_page"], second["remaining_pages"], second["next"]), (2, 0, None))
        self.assertEqual(len(second["results"]), 1)

    def test_page_size_is_1000(self):
        self.assertEqual(LinkDetailPagination.page_size, 1000)
