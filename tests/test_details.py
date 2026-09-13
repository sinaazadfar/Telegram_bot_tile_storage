from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from bot.handlers.details import (
    catalog_caption,
    chunk_report_sections,
    details_list,
    details_menu_buttons,
    sellable_total,
    send_available_catalogs,
)
from bot.strings import (
    AVAILABLE_CATALOGS_TEXT,
    DETAILS_ALL_PDF_OUTPUT,
    DETAILS_ALL_TEXT,
    DETAILS_ALL_TEXT_OUTPUT,
)


class DetailsHelpersTests(TestCase):
    def test_text_report_chunks_stay_within_telegram_limit(self) -> None:
        chunks = chunk_report_sections(["a" * 2500, "b" * 2500, "c" * 100])

        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 4000 for chunk in chunks))
        self.assertIn("a" * 100, chunks[0])
        self.assertIn("b" * 100, chunks[1])

    def test_menu_places_catalog_button_beside_output(self) -> None:
        buttons = details_menu_buttons(DETAILS_ALL_TEXT, {"طرح ۱": [{}]})

        self.assertEqual(
            buttons[0], [DETAILS_ALL_TEXT_OUTPUT, DETAILS_ALL_PDF_OUTPUT]
        )
        self.assertEqual(buttons[1], [AVAILABLE_CATALOGS_TEXT])
        self.assertEqual(buttons[2], ["طرح ۱"])

    def test_sellable_total_parses_meter_and_pallet(self) -> None:
        details = [("مجموع طرح (قابل فروش)", "(14.43) 1,402.92")]

        self.assertEqual(sellable_total(details), ("1,402.92", "14.43"))

    def test_sellable_total_rejects_non_positive_or_invalid_bulk_values(self) -> None:
        for value in ("0", "(0) 0", "نامعتبر", ""):
            with self.subTest(value=value):
                self.assertIsNone(
                    sellable_total(
                        [("مجموع طرح (قابل فروش)", value)], positive_only=True
                    )
                )

    def test_zero_sellable_total_remains_available_for_single_caption(self) -> None:
        details = [("مجموع طرح (قابل فروش)", "(0) 0")]

        self.assertEqual(sellable_total(details), ("0", "0"))
        self.assertIsNone(sellable_total(details, positive_only=True))

    def test_sellable_total_ignores_other_inventory_metrics(self) -> None:
        details = [("مجموع طرح (فیزیکی)", "(2) 200")]

        self.assertIsNone(sellable_total(details))

    def test_catalog_caption_contains_total_sellable_amount(self) -> None:
        target = {"name_display": "دکور رها", "code_display": "2835"}

        caption = catalog_caption(target, ("1402.92", "14.43"))

        self.assertEqual(
            caption,
            "نام طرح: دکور رها\n"
            "کد طرح: 2835\n"
            "موجودی قابل فروش: 1402.92 متر مربع (14.43 پالت)",
        )


class AvailableCatalogTests(IsolatedAsyncioTestCase):
    @patch("bot.handlers.details.send_text", new_callable=AsyncMock)
    @patch("bot.handlers.details.send_catalog_images", new_callable=AsyncMock)
    @patch("bot.handlers.details.get_output_row_details")
    async def test_only_positive_sellable_rows_are_sent(
        self,
        get_details,
        send_images: AsyncMock,
        send_text_mock: AsyncMock,
    ) -> None:
        rows = [{"code_display": "1"}, {"code_display": "2"}]
        get_details.side_effect = [
            [("مجموع طرح (قابل فروش)", "(2) 200")],
            [("مجموع طرح (قابل فروش)", "0")],
        ]
        send_images.return_value = True
        context = SimpleNamespace(user_data={"warehouse": "fakhar"})
        update = SimpleNamespace(effective_user=None)

        result = await send_available_catalogs(
            update, context, rows, Path("output.xlsx")
        )

        self.assertEqual(result, ConversationHandler.END)
        send_images.assert_awaited_once_with(
            update,
            context,
            rows[0],
            total=("200", "2"),
            notify_missing=False,
        )
        self.assertFalse(context.user_data["conversation_active"])
        self.assertIn("1", send_text_mock.await_args.args[1])

    @patch("bot.handlers.details.send_text", new_callable=AsyncMock)
    @patch("bot.handlers.details.send_catalog_images", new_callable=AsyncMock)
    @patch("bot.handlers.details.get_output_row_details")
    async def test_reports_when_eligible_plan_has_no_catalog(
        self,
        get_details,
        send_images: AsyncMock,
        send_text_mock: AsyncMock,
    ) -> None:
        row = {"code_display": "1"}
        get_details.return_value = [("مجموع طرح (قابل فروش)", "(2) 200")]
        send_images.return_value = False
        context = SimpleNamespace(user_data={"warehouse": "fakhar"})
        update = SimpleNamespace(effective_user=None)

        await send_available_catalogs(update, context, [row], Path("output.xlsx"))

        self.assertEqual(
            send_text_mock.await_args.args[1],
            "برای طرح‌های دارای موجودی قابل فروش، کاتالوگی پیدا نشد.",
        )

    @patch("bot.handlers.details.send_text", new_callable=AsyncMock)
    @patch("bot.handlers.details.warehouse_output_path")
    @patch("bot.handlers.details.ensure_warehouse_template_path")
    async def test_catalog_button_reports_missing_output_file(
        self,
        template_path_mock,
        output_path_mock,
        send_text_mock: AsyncMock,
    ) -> None:
        template_path_mock.return_value = Path("template.xlsx")
        output_path_mock.return_value = SimpleNamespace(exists=lambda: False)
        update = SimpleNamespace(
            message=SimpleNamespace(text=AVAILABLE_CATALOGS_TEXT),
            effective_user=None,
        )
        context = SimpleNamespace(
            user_data={"warehouse": "fakhar", "conversation_active": True}
        )

        result = await details_list(update, context)

        self.assertEqual(result, ConversationHandler.END)
        self.assertEqual(send_text_mock.await_args.args[1], "فایل خروجی پیدا نشد.")
        self.assertFalse(context.user_data["conversation_active"])

    @patch("bot.handlers.details.send_available_catalogs", new_callable=AsyncMock)
    @patch("bot.handlers.details.warehouse_output_path")
    @patch("bot.handlers.details.ensure_warehouse_template_path")
    async def test_catalog_button_uses_current_scope_rows(
        self,
        template_path_mock,
        output_path_mock,
        send_available: AsyncMock,
    ) -> None:
        scoped_rows = [{"code_display": "filtered"}]
        template_path_mock.return_value = Path("template.xlsx")
        output_path = SimpleNamespace(exists=lambda: True)
        output_path_mock.return_value = output_path
        send_available.return_value = ConversationHandler.END
        update = SimpleNamespace(message=SimpleNamespace(text=AVAILABLE_CATALOGS_TEXT))
        context = SimpleNamespace(
            user_data={
                "warehouse": "fakhar",
                "details_catalog_rows": scoped_rows,
            }
        )

        result = await details_list(update, context)

        self.assertEqual(result, ConversationHandler.END)
        send_available.assert_awaited_once_with(
            update, context, scoped_rows, output_path
        )
