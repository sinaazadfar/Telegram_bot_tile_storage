import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..keyboards import main_keyboard, manage_rows_keyboard, warehouse_menu_keyboard
from ..strings import WAREHOUSE_BY_LABEL
from ..text import send_text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = "یک انبار را انتخاب کنید."
    await send_text(update, message, reply_markup=main_keyboard())
    context.user_data["menu_level"] = "main"


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "راهنما:\n"
        "- یک فایل .xlsx یا .pdf ارسال کنید\n"
        "- ربات خروجی را بر اساس تمپلیت برمی‌گرداند\n"
    )
    await send_text(update, message)


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.pop("skip_back_once", False):
        return
    if context.user_data.get("conversation_active"):
        return
    level = context.user_data.get("menu_level")
    if level == "manage_rows":
        await send_text(update, "به منوی انبار برگشتید.", reply_markup=warehouse_menu_keyboard())
        context.user_data["menu_level"] = "warehouse"
        return
    if level == "warehouse":
        await send_text(update, "به منوی اصلی برگشتید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return
    await send_text(update, "به منوی اصلی برگشتید.", reply_markup=main_keyboard())
    context.user_data["menu_level"] = "main"


async def manage_rows(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get("warehouse"):
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return
    await send_text(update, "مدیریت طرح‌ها:", reply_markup=manage_rows_keyboard())
    context.user_data["menu_level"] = "manage_rows"


async def select_warehouse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    key = WAREHOUSE_BY_LABEL.get(text)
    if not key:
        await send_text(update, "یکی از انبارها را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return
    context.user_data["warehouse"] = key
    await send_text(update, f"{text} انتخاب شد.", reply_markup=warehouse_menu_keyboard())
    context.user_data["menu_level"] = "warehouse"


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await send_text(update, "خطایی رخ داد. دوباره تلاش کنید.")
