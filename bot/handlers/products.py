import asyncio
import logging

from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from build_output import process_files

from ..config import (
    ALLOWED_METRICS,
    DEFAULT_METRIC,
    PROCESS_TIMEOUT,
    warehouse_input_path,
    warehouse_output_path,
    ensure_warehouse_template_path,
)
from ..keyboards import main_keyboard, manage_menu_keyboard, products_menu_keyboard
from ..strings import (
    BACK_TEXT,
    PRODUCTS_DOWNLOAD_TEXT,
    PRODUCTS_MENU_TEXT,
    PRODUCTS_UPLOAD_TEXT,
)
from ..text import send_text

STATE_PRODUCTS_MENU, STATE_PRODUCTS_WAIT_FILE = range(2)


async def products_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("warehouse"):
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    context.user_data["conversation_active"] = True
    context.user_data["menu_level"] = "manage_menu"
    await send_text(update, "مدیریت فایل محصولات:", reply_markup=products_menu_keyboard())
    return STATE_PRODUCTS_MENU


async def products_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BACK_TEXT:
        await send_text(update, "به منوی تنظیمات برگشتید.", reply_markup=manage_menu_keyboard())
        context.user_data["skip_back_once"] = True
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    if text == PRODUCTS_UPLOAD_TEXT:
        await send_text(
            update,
            "فایل اکسل محصولات را ارسال کنید.",
            reply_markup=products_menu_keyboard(),
        )
        return STATE_PRODUCTS_WAIT_FILE
    if text == PRODUCTS_DOWNLOAD_TEXT:
        output_path = warehouse_output_path(context.user_data["warehouse"])
        if not output_path.exists():
            await send_text(update, "فایل مرتب‌شده موجود نیست.")
            return STATE_PRODUCTS_MENU
        with output_path.open("rb") as handle:
            await update.message.reply_document(
                document=handle,
                filename=output_path.name,
            )
        return STATE_PRODUCTS_MENU
    await send_text(update, "یکی از گزینه‌ها را انتخاب کنید.")
    return STATE_PRODUCTS_MENU


async def products_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.document:
        await send_text(update, "فایل .xlsx یا .pdf ارسال کنید یا برگشت بزنید.")
        return STATE_PRODUCTS_WAIT_FILE
    document = update.message.document
    filename = document.file_name or ""
    if not (filename.lower().endswith(".xlsx") or filename.lower().endswith(".pdf")):
        await send_text(update, "فایل باید .xlsx یا .pdf باشد.")
        return STATE_PRODUCTS_WAIT_FILE
    template_path = ensure_warehouse_template_path(context.user_data["warehouse"])
    if not template_path:
        await send_text(update, "تمپلیت پیدا نشد.")
        context.user_data["conversation_active"] = False
        return ConversationHandler.END
    metric = DEFAULT_METRIC if DEFAULT_METRIC in ALLOWED_METRICS else "physical"
    suffix = ".pdf" if filename.lower().endswith(".pdf") else ".xlsx"
    input_path = warehouse_input_path(context.user_data["warehouse"], suffix)
    output_path = warehouse_output_path(context.user_data["warehouse"])
    try:
        file_obj = await document.get_file()
        await file_obj.download_to_drive(custom_path=str(input_path))
        loop = asyncio.get_running_loop()
        processing_task = loop.run_in_executor(
            None,
            process_files,
            input_path,
            template_path,
            output_path,
            metric,
            None,
            False,
        )
        if PROCESS_TIMEOUT:
            await asyncio.wait_for(processing_task, timeout=PROCESS_TIMEOUT)
        else:
            await processing_task
        await send_text(
            update,
            "فایل مرتب‌شده ذخیره شد. برای دریافت، دکمه مربوطه را بزنید.",
            reply_markup=products_menu_keyboard(),
        )
    except asyncio.TimeoutError:
        logging.exception("Timeout while processing products file.")
        await send_text(update, "زمان پردازش تمام شد. دوباره تلاش کنید.")
    except (TimedOut, NetworkError):
        logging.exception("Telegram API request failed.")
        await send_text(update, "مشکل شبکه. دوباره تلاش کنید.")
    except Exception:
        logging.exception("Failed to process products file.")
        await send_text(update, "پردازش انجام نشد. دوباره تلاش کنید.")
    return STATE_PRODUCTS_MENU


def build_products_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{PRODUCTS_MENU_TEXT}$"), products_start)
        ],
        states={
            STATE_PRODUCTS_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, products_menu)
            ],
            STATE_PRODUCTS_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, products_receive_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, products_menu),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{BACK_TEXT}$"), products_menu),
            CommandHandler("cancel", products_menu),
        ],
    )
