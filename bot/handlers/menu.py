import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from ..keyboards import (
    main_keyboard,
    manage_menu_keyboard,
    manage_rows_keyboard,
    warehouse_menu_keyboard,
)
from ..strings import WAREHOUSE_BY_LABEL
from ..text import send_text
from ..auth import add_admin, is_admin
from ..config import resolve_warehouse_input_path
from ..utils import format_jalali_datetime


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


async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await send_text(update, "فقط مدیر می‌تواند مدیر جدید اضافه کند.")
        return
    target = update.message.reply_to_message.from_user if update.message and update.message.reply_to_message else None
    if target is None and context.args:
        try:
            user_id = int(context.args[0])
        except ValueError:
            await send_text(update, "شناسه عددی تلگرام معتبر نیست.")
            return
    elif target is not None:
        user_id = target.id
    else:
        await send_text(update, "روی پیام کاربر پاسخ دهید و /addadmin را بفرستید، یا /addadmin USER_ID را وارد کنید.")
        return
    add_admin(user_id)
    await send_text(update, f"کاربر {user_id} به مدیران اضافه شد.")


async def user_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    replied_user = (
        update.message.reply_to_message.from_user
        if update.message and update.message.reply_to_message
        else None
    )
    if replied_user:
        await send_text(update, f"شناسه کاربر: {replied_user.id}")
        return
    await send_text(update, f"شناسه شما: {update.effective_user.id}")


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.pop("skip_back_once", False):
        return
    if context.user_data.get("conversation_active"):
        return
    level = context.user_data.get("menu_level")
    if level == "manage_rows":
        await send_text(update, "به منوی تنظیمات برگشتید.", reply_markup=manage_menu_keyboard())
        context.user_data["menu_level"] = "manage_menu"
        return
    if level == "manage_menu":
        await send_text(update, "به منوی انبار برگشتید.", reply_markup=warehouse_menu_keyboard(is_admin(update)))
        context.user_data["menu_level"] = "warehouse"
        return
    if level == "warehouse":
        await send_text(update, "به منوی اصلی برگشتید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return
    await send_text(update, "به منوی اصلی برگشتید.", reply_markup=main_keyboard())
    context.user_data["menu_level"] = "main"


async def manage_rows(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await send_text(update, "شما دسترسی ویرایش ندارید.", reply_markup=warehouse_menu_keyboard(False))
        return
    if not context.user_data.get("warehouse"):
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return
    await send_text(update, "مدیریت طرح‌ها:", reply_markup=manage_rows_keyboard())
    context.user_data["menu_level"] = "manage_rows"


async def manage_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await send_text(update, "شما دسترسی ویرایش ندارید.", reply_markup=warehouse_menu_keyboard(False))
        return
    if not context.user_data.get("warehouse"):
        await send_text(update, "اول انبار را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return
    await send_text(update, "تنظیمات:", reply_markup=manage_menu_keyboard())
    context.user_data["menu_level"] = "manage_menu"


async def select_warehouse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    key = WAREHOUSE_BY_LABEL.get(text)
    if not key:
        await send_text(update, "یکی از انبارها را انتخاب کنید.", reply_markup=main_keyboard())
        context.user_data["menu_level"] = "main"
        return
    context.user_data["warehouse"] = key
    input_path = resolve_warehouse_input_path(key)
    if input_path:
        modified = datetime.fromtimestamp(input_path.stat().st_mtime, ZoneInfo("Asia/Tehran"))
        status = f"\nآخرین فایل ورودی: {format_jalali_datetime(modified)}"
    else:
        status = "\nهنوز فایل ورودی ثبت نشده است."
    await send_text(update, f"{text} انتخاب شد.{status}", reply_markup=warehouse_menu_keyboard(is_admin(update)))
    context.user_data["menu_level"] = "warehouse"


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await send_text(update, "خطایی رخ داد. دوباره تلاش کنید.")
