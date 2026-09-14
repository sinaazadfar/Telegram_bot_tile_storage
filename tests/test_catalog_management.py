import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import openpyxl

from bot.formatting import build_label_map
from bot.handlers.catalogs import STATE_CATALOG_SELECT, catalogs_menu, filter_catalog_targets
from bot.storage import (
    DuplicatePlanCodeError,
    append_template_row,
    list_template_rows,
    update_template_row,
)
from bot.strings import CATALOG_UPSERT_TEXT


def make_template(path: Path, rows: list[tuple]) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["code", "name", "size", "divisor"])
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()


class TemplateUniquenessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self.temp_dir.name) / "template.xlsx"
        make_template(self.path, [(2803, "دکور رامونا سفید", "30*60", 97.2)])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_append_rejects_an_existing_normalized_code(self) -> None:
        with self.assertRaises(DuplicatePlanCodeError):
            append_template_row("2803.0", "طرح دیگر", "20*20", 10, self.path)

    def test_update_rejects_code_owned_by_another_row(self) -> None:
        append_template_row("1000", "طرح دوم", "20*20", 10, self.path)
        second = list_template_rows(self.path)[1]
        with self.assertRaises(DuplicatePlanCodeError):
            update_template_row(
                second,
                {"code": "2803", "name": "طرح دوم", "size": "20*20", "divisor": 10},
                self.path,
            )

    def test_identical_legacy_duplicates_have_one_selectable_target(self) -> None:
        make_template(
            self.path,
            [
                (2803, "دکور رامونا سفید ", "30*60", 97.2),
                (2803, "دکور رامونا  سفید", "30*60", 97.2),
            ],
        )
        label_map = build_label_map(list_template_rows(self.path))
        self.assertEqual(len(label_map["دکور رامونا سفید (2803)"]), 1)


class CatalogPlanListTest(unittest.IsolatedAsyncioTestCase):
    @patch("bot.handlers.catalogs.list_catalog_images")
    def test_filter_is_applied_to_catalog_search_results(self, list_images_mock) -> None:
        rows = [{"code_display": "100"}, {"code_display": "200"}]
        list_images_mock.side_effect = lambda warehouse, row: (
            [Path("img.jpg")] if row["code_display"] == "200" else []
        )

        self.assertEqual(filter_catalog_targets("fakhar", "upsert", rows), [rows[0]])
        self.assertEqual(filter_catalog_targets("fakhar", "delete", rows), [rows[1]])

    @patch("bot.handlers.catalogs.send_text", new_callable=AsyncMock)
    @patch("bot.handlers.catalogs.list_catalog_images")
    @patch("bot.handlers.catalogs.ensure_warehouse_template_path")
    async def test_upsert_lists_only_plans_without_catalog(
        self, template_path_mock, list_images_mock, send_text_mock
    ) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            path = Path(temp_dir) / "template.xlsx"
            make_template(path, [(100, "بدون کاتالوگ", "1", 1), (200, "با کاتالوگ", "1", 1)])
            template_path_mock.return_value = path
            list_images_mock.side_effect = lambda warehouse, row: (
                [Path("img.jpg")] if row["code_display"] == "200" else []
            )
            update = MagicMock()
            update.message.text = CATALOG_UPSERT_TEXT
            context = MagicMock()
            context.user_data = {"warehouse": "fakhar"}

            state = await catalogs_menu(update, context)

        self.assertEqual(state, STATE_CATALOG_SELECT)
        label_map = context.user_data["catalog_label_map"]
        self.assertEqual(list(label_map), ["بدون کاتالوگ (100)"])
        send_text_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
