from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

ENV = {"DJANGO_SUPERUSER_USERNAME": "boss", "DJANGO_SUPERUSER_PASSWORD": "first-pass-123"}


def run_ensure_admin(env: dict) -> str:
    out = StringIO()
    with mock.patch.dict("os.environ", env, clear=True):
        call_command("ensure_admin", stdout=out)
    return out.getvalue()


class EnsureAdminTests(TestCase):
    def test_creates_a_superuser_from_the_environment(self):
        run_ensure_admin(ENV)

        user = get_user_model().objects.get(username="boss")
        self.assertTrue(user.is_staff and user.is_superuser)
        self.assertTrue(user.check_password("first-pass-123"))

    def test_second_run_keeps_the_existing_user_and_password(self):
        run_ensure_admin(ENV)
        output = run_ensure_admin({**ENV, "DJANGO_SUPERUSER_PASSWORD": "another-pass-456"})

        self.assertIn("already exists", output)
        user = get_user_model().objects.get(username="boss")
        self.assertTrue(user.check_password("first-pass-123"))

    def test_without_variables_no_user_is_created(self):
        output = run_ensure_admin({})

        self.assertIn("not set", output)
        self.assertEqual(get_user_model().objects.count(), 0)
