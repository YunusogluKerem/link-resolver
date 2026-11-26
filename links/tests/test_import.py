import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook

from links.excel import parse_content_id, parse_links_cell
from links.models import Links


class ParseLinksCellTests(SimpleTestCase):
    def test_strips_array_braces(self):
        self.assertEqual(parse_links_cell("{https://ty.gl/abc?x=1}"), ["https://ty.gl/abc?x=1"])

    def test_quoted_element_keeps_its_comma_and_loses_padding(self):
        cell = '{"https://t24.com.tr/yazi,60064?utm=Instagram "}'
        self.assertEqual(parse_links_cell(cell), ["https://t24.com.tr/yazi,60064?utm=Instagram"])

    def test_several_elements(self):
        self.assertEqual(parse_links_cell('{https://a.com,"https://b.com/x,y"}'), ["https://a.com", "https://b.com/x,y"])

    def test_empty_and_null_values_give_nothing(self):
        for cell in (None, "", "   ", "{}", "{NULL}"):
            self.assertEqual(parse_links_cell(cell), [], msg=repr(cell))

    def test_plain_url_without_braces(self):
        self.assertEqual(parse_links_cell(" https://a.com "), ["https://a.com"])


class ParseContentIdTests(SimpleTestCase):
    def test_accepts_int_float_and_digit_text(self):
        self.assertEqual(parse_content_id(123456789), 123456789)
        self.assertEqual(parse_content_id(123456789.0), 123456789)
        self.assertEqual(parse_content_id(" 42 "), 42)

    def test_rejects_everything_else(self):
        for value in (None, "", "abc", 1.5, True):
            self.assertIsNone(parse_content_id(value), msg=repr(value))


class ImportLinksCommandTests(TestCase):
    def _import(self, rows) -> str:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "links.xlsx"
            workbook = Workbook()
            for row in [("id", "links"), *rows]:
                workbook.active.append(row)
            workbook.save(path)
            out = StringIO()
            call_command("import_links", str(path), stdout=out)
            return out.getvalue()

    def test_imports_clean_links_and_skips_empty_and_duplicate_rows(self):
        self._import(
            [
                (1, "{https://ty.gl/a}"),
                (2, "{https://ty.gl/a}"),  # same link, other content: kept
                (2, "{https://ty.gl/a}"),  # exact repeat: skipped
                (3, None),  # no link: skipped
                (None, "{https://ty.gl/b}"),  # no id: skipped
            ]
        )
        self.assertEqual(
            list(Links.objects.values_list("content_id", "link")),
            [(1, "https://ty.gl/a"), (2, "https://ty.gl/a")],
        )

    def test_running_twice_does_not_duplicate(self):
        rows = [(1, "{https://ty.gl/a}"), (2, "{https://ty.gl/b}")]
        self._import(rows)
        output = self._import(rows)
        self.assertEqual(Links.objects.count(), 2)
        self.assertIn("Imported:                0", output)
